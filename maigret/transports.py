# Standard library imports
import asyncio
import logging
import os
import random
import re
import ssl
import sys
from typing import Any, Dict, Optional, Tuple
from urllib.parse import quote

# Third party imports
import aiodns
from aiohttp import ClientSession, TCPConnector, http_exceptions
from aiohttp.resolver import ThreadedResolver
from aiohttp.client_exceptions import ClientConnectorError, ServerDisconnectedError
from python_socks import _errors as proxy_errors

try:
    from aiohttp.client_exceptions import ClientConnectorDNSError  # type: ignore
except ImportError:  # aiohttp < 3.10
    ClientConnectorDNSError = None  # type: ignore[assignment,misc]

try:
    from mock import Mock
except ImportError:
    from unittest.mock import Mock

from .errors import CheckError
from .types import QueryOptions

_DNS_ERROR_MARKERS = (
    "could not contact dns servers",
    "name or service not known",
    "nodename nor servname",
    "temporary failure in name resolution",
    "getaddrinfo failed",
)


def _is_dns_error(exc: Exception) -> bool:
    """Classify a connector failure as DNS-related when possible."""
    if ClientConnectorDNSError is not None and isinstance(exc, ClientConnectorDNSError):
        return True
    text = str(exc).lower()
    return any(marker in text for marker in _DNS_ERROR_MARKERS)


class CheckerBase:
    pass


class SimpleAiohttpChecker(CheckerBase):
    def __init__(self, *args, **kwargs):
        self.proxy = kwargs.get('proxy')
        self.cookie_jar = kwargs.get('cookie_jar')
        self.logger = kwargs.get('logger', Mock())
        # 'async' (default) uses aiohttp's DefaultResolver, which is AsyncResolver
        # (powered by aiodns / c-ares) when aiodns is installed. 'threaded' uses
        # ThreadedResolver, which wraps the OS getaddrinfo via a threadpool —
        # slower for high concurrency, but respects the system DNS config
        # (resolv.conf, Windows network adapter settings) instead of having
        # aiodns rediscover it. See issue #2688: aiodns can fail to find any
        # DNS server on Windows / VPN / corporate networks, producing
        # "Could not contact DNS servers" for every site.
        self.dns_resolver = kwargs.get('dns_resolver', 'async')
        self.url = None
        self.headers = None
        self.allow_redirects = True
        self.timeout = 0
        self.method = 'get'
        self.payload = None

    def prepare(self, url, headers=None, allow_redirects=True, timeout=0, method='get', payload=None):
        self.url = url
        self.headers = headers
        self.allow_redirects = allow_redirects
        self.timeout = timeout
        self.method = method
        self.payload = payload
        return None

    async def close(self):
        pass

    async def _make_request(
        self, session, url, headers, allow_redirects, timeout, method, logger, payload=None
    ) -> Tuple[Optional[str], int, Optional[CheckError]]:
        try:
            if method.lower() == 'get':
                request_method = session.get
            elif method.lower() == 'post':
                request_method = session.post
            elif method.lower() == 'head':
                request_method = session.head
            else:
                request_method = session.get

            kwargs = {
                'url': url,
                'headers': headers,
                'allow_redirects': allow_redirects,
                'timeout': timeout,
            }
            if payload and method.lower() == 'post':
                if headers and headers.get('Content-Type') == 'application/x-www-form-urlencoded':
                    kwargs['data'] = payload
                else:
                    kwargs['json'] = payload

            async with request_method(**kwargs) as response:
                status_code = response.status
                response_content = await response.content.read()
                charset = response.charset or "utf-8"
                decoded_content = response_content.decode(charset, "ignore")

                error = CheckError("Connection lost") if status_code == 0 else None
                logger.debug(decoded_content)

                return decoded_content, status_code, error

        except asyncio.TimeoutError as e:
            return None, 0, CheckError("Request timeout", str(e))
        except ClientConnectorError as e:
            err_type = "Connecting failure (DNS)" if _is_dns_error(e) else "Connecting failure"
            return None, 0, CheckError(err_type, str(e))
        except ServerDisconnectedError as e:
            return None, 0, CheckError("Server disconnected", str(e))
        except http_exceptions.BadHttpMessage as e:
            return None, 0, CheckError("HTTP", str(e))
        except proxy_errors.ProxyError as e:
            return None, 0, CheckError("Proxy", str(e))
        except KeyboardInterrupt:
            return None, 0, CheckError("Interrupted")
        except Exception as e:
            if sys.version_info.minor > 6 and (
                isinstance(e, ssl.SSLCertVerificationError)
                or isinstance(e, ssl.SSLError)
            ):
                return None, 0, CheckError("SSL", str(e))
            else:
                logger.debug(e, exc_info=True)
                return None, 0, CheckError("Unexpected", str(e))

    async def check(self) -> Tuple[Optional[str], int, Optional[CheckError]]:
        from aiohttp_socks import ProxyConnector

        # Use a real SSL context instead of ssl=False to avoid TLS fingerprinting
        # blocks by Cloudflare and similar WAFs. Certificate verification is
        # disabled to handle sites with invalid/expired certs.
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE

        # Build the TCPConnector with an explicit resolver when 'threaded' is
        # requested. ProxyConnector takes its own resolver kwarg too, so apply
        # the same setting on both code paths.
        resolver = ThreadedResolver() if self.dns_resolver == 'threaded' else None
        if self.proxy:
            connector = ProxyConnector.from_url(self.proxy, resolver=resolver) if resolver else ProxyConnector.from_url(self.proxy)
        else:
            connector = TCPConnector(ssl=ssl_context, resolver=resolver) if resolver else TCPConnector(ssl=ssl_context)

        async with ClientSession(
            connector=connector,
            trust_env=True,
            # TODO: tests
            cookie_jar=self.cookie_jar if self.cookie_jar else None,
        ) as session:
            html_text, status_code, error = await self._make_request(
                session,
                self.url,
                self.headers,
                self.allow_redirects,
                self.timeout,
                self.method,
                self.logger,
                self.payload,
            )

            if error and str(error) == "Invalid proxy response":
                self.logger.debug(error, exc_info=True)

            return str(html_text) if html_text else '', status_code, error


class ProxiedAiohttpChecker(SimpleAiohttpChecker):
    pass


class AiodnsDomainResolver(CheckerBase):
    if sys.platform == 'win32':  # Temporary workaround for Windows
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    def __init__(self, *args, **kwargs):
        loop = asyncio.get_event_loop()
        self.logger = kwargs.get('logger', Mock())
        self.resolver = aiodns.DNSResolver(loop=loop)

    def prepare(self, url, headers=None, allow_redirects=True, timeout=0, method='get', payload=None):
        self.url = url
        return None

    async def check(self) -> Tuple[Optional[str], int, Optional[CheckError]]:
        status = 404
        error = None
        text = ''

        try:
            res = await self.resolver.query(self.url, 'A')
            text = str(res[0].host)
            status = 200
        except aiodns.error.DNSError:
            pass
        except Exception as e:
            self.logger.error(e, exc_info=True)
            error = CheckError('DNS resolve error', str(e))

        return text, status, error


try:
    from curl_cffi.requests import AsyncSession as CurlCffiAsyncSession

    CURL_CFFI_AVAILABLE = True
except ImportError:
    CURL_CFFI_AVAILABLE = False


class CurlCffiChecker(CheckerBase):
    """Checker using curl_cffi to emulate browser TLS fingerprint and bypass WAF."""

    def __init__(self, *args, **kwargs):
        self.logger = kwargs.get('logger', Mock())
        self.browser_emulate = kwargs.get('browser_emulate', 'chrome')
        self.proxy = kwargs.get('proxy')
        self.url = None
        self.headers = None
        self.allow_redirects = True
        self.timeout = 0
        self.method = 'get'
        self.payload = None

    def prepare(self, url, headers=None, allow_redirects=True, timeout=0, method='get', payload=None):
        self.url = url
        self.headers = headers
        self.allow_redirects = allow_redirects
        self.timeout = timeout
        self.method = method
        self.payload = payload
        return None

    async def close(self):
        pass

    async def check(self) -> Tuple[Optional[str], int, Optional[CheckError]]:
        try:
            session_kwargs = {}
            if self.proxy:
                session_kwargs['proxies'] = {'http': self.proxy, 'https': self.proxy}
            async with CurlCffiAsyncSession(**session_kwargs) as session:
                # Strip the User-Agent so curl_cffi can use the impersonated browser's
                # matching UA. Mixing a random UA with a Chrome TLS fingerprint trips
                # composite bot scoring (e.g. Cloudflare returns a JS challenge for
                # "Chrome 91 UA + Chrome 131 TLS"). Keep any site-specific custom headers.
                headers = {k: v for k, v in (self.headers or {}).items()
                           if k.lower() not in ('user-agent', 'connection')}
                kwargs = {
                    'url': self.url,
                    'headers': headers or None,
                    'allow_redirects': self.allow_redirects,
                    'timeout': self.timeout if self.timeout else 10,
                    'impersonate': self.browser_emulate,
                }
                if self.payload and self.method.lower() == 'post':
                    kwargs['json'] = self.payload

                if self.method.lower() == 'post':
                    response = await session.post(**kwargs)
                elif self.method.lower() == 'head':
                    response = await session.head(**kwargs)
                else:
                    response = await session.get(**kwargs)

                status_code = response.status_code
                decoded_content = response.text

                self.logger.debug(decoded_content)

                error = CheckError("Connection lost") if status_code == 0 else None
                return decoded_content, status_code, error

        except asyncio.TimeoutError as e:
            return None, 0, CheckError("Request timeout", str(e))
        except KeyboardInterrupt:
            return None, 0, CheckError("Interrupted")
        except Exception as e:
            self.logger.debug(e, exc_info=True)
            return None, 0, CheckError("Unexpected", str(e))


class CloudflareWebgateChecker(CheckerBase):
    """Sends checks through a Cloudflare-bypass proxy.

    Supports two backends, selected by ``modules[0].method`` in settings:

    - ``json_api`` (FlareSolverr): POST to ``/v1`` with ``cmd: request.get``.
      Preserves real upstream status_code, headers and final URL — drop-in
      replacement for SimpleAiohttpChecker.
    - ``url_rewrite`` (CloudflareBypassForScraping ``/html`` endpoint):
      legacy mode. Returns rendered HTML only. Real upstream status is
      lost (proxy answers 200 on success). status_code / response_url
      check types degrade to "200 if HTML returned, AVAILABLE otherwise".
    """

    SESSION_PREFIX_DEFAULT = "maigret"

    def __init__(self, *args, **kwargs):
        self.logger = kwargs.get('logger', Mock())
        config = kwargs.get('config') or {}
        self._modules: List[Dict[str, Any]] = []
        for raw in config.get('modules') or []:
            module = dict(raw)
            module.setdefault('method', 'json_api')
            module.setdefault('name', module.get('method'))
            self._modules.append(module)
        if not self._modules:
            raise ValueError("CloudflareWebgateChecker requires at least one module")
        # Session ID is computed per-request from the target host. Sharing a
        # single session across hosts caused FlareSolverr to break in
        # practice (TLS state / cookies leaking between domains), so each
        # host gets its own Chrome instance.
        self._session_prefix = (
            f"{config.get('session_prefix', self.SESSION_PREFIX_DEFAULT)}-{os.getpid()}"
        )
        self.url = None
        self.headers = None
        self.allow_redirects = True
        self.timeout = 0
        self.method = 'get'
        self.payload = None

    @property
    def session_id(self) -> str:
        """FlareSolverr session ID, scoped per target host."""
        from urllib.parse import urlparse

        host = urlparse(self.url or "").hostname or "default"
        host_safe = re.sub(r"[^a-zA-Z0-9.-]", "_", host)
        return f"{self._session_prefix}-{host_safe}"

    def prepare(self, url, headers=None, allow_redirects=True, timeout=0, method='get', payload=None):
        self.url = url
        self.headers = headers or {}
        self.allow_redirects = allow_redirects
        self.timeout = timeout
        self.method = method
        self.payload = payload
        return None

    async def close(self):
        pass

    async def check(self) -> Tuple[Optional[str], int, Optional[CheckError]]:
        attempts: List[str] = []
        last_error: Optional[CheckError] = None
        for module in self._modules:
            method = module.get('method')
            module_name = module.get('name', method or '?')
            if method == 'json_api':
                result = await self._check_flaresolverr(module)
            elif method == 'url_rewrite':
                result = await self._check_url_rewrite(module)
            else:
                self.logger.warning(
                    f"Webgate module '{module_name}' has unknown method "
                    f"'{method}', skipping"
                )
                attempts.append(f"{module_name}:unknown-method")
                continue
            body, status, err = result
            if err is None:
                return result
            last_error = err
            attempts.append(f"{module_name}:{err.type}")
            self.logger.info(
                f"Webgate module '{module_name}' failed for {self.url}: "
                f"{err.type}: {err.desc}. Trying next module if any."
            )
        # All modules failed. The most common case is "user opted into
        # cloudflare_bypass but the solver isn't running" — every per-module
        # attempt ends with "Webgate unreachable" (TCP refused / DNS fail at
        # the configured URL). Detect that case and emit a clear, actionable
        # message; fall back to a generic summary otherwise.
        primary = self._modules[0]
        primary_url = primary.get('url', '?')
        primary_method = primary.get('method', '?')
        start_hint = (
            "docker run -d -p 8191:8191 ghcr.io/flaresolverr/flaresolverr:latest"
            if primary_method == 'json_api'
            else "start the local proxy container"
        )
        all_unreachable = bool(attempts) and all(
            a.endswith(":Webgate unreachable") for a in attempts
        )
        if all_unreachable:
            desc = (
                "cloudflare_bypass is enabled (settings.json or "
                f"--cloudflare-bypass), but the configured solver at "
                f"{primary_url} is not reachable [{', '.join(attempts)}]. "
                f"Either start the solver ({start_hint}) or disable "
                "cloudflare_bypass in settings.json"
            )
        else:
            last_desc = last_error.desc if last_error else "unknown"
            desc = (
                f"all {len(self._modules)} module(s) failed "
                f"[{', '.join(attempts)}]. Last error: {last_desc}. "
                f"Is the solver running at {primary_url}? (hint: {start_hint})"
            )
        return None, 0, CheckError("Webgate unavailable", desc)

    async def _check_flaresolverr(
        self, module: Dict[str, Any]
    ) -> Tuple[Optional[str], int, Optional[CheckError]]:
        endpoint = module.get('url') or 'http://localhost:8191/v1'
        max_timeout_ms = int(module.get('max_timeout_ms', 60000))
        post_method = self.method.lower() == 'post'
        cmd = "request.post" if post_method else "request.get"

        body: Dict[str, Any] = {
            "cmd": cmd,
            "url": self.url,
            "maxTimeout": max_timeout_ms,
            "session": self.session_id,
        }

        proxy = module.get('proxy')
        if isinstance(proxy, str) and proxy:
            body["proxy"] = {"url": proxy}
        elif isinstance(proxy, dict) and proxy.get("url"):
            body["proxy"] = {k: v for k, v in proxy.items() if k in ("url", "username", "password")}

        if post_method and self.payload is not None:
            # FlareSolverr expects postData as urlencoded string for form data,
            # but if site.request_payload is JSON we still send it.
            body["postData"] = (
                "&".join(f"{k}={quote(str(v))}" for k, v in self.payload.items())
            )

        timeout = max(int(self.timeout) if self.timeout else 30, max_timeout_ms / 1000 + 5)

        try:
            async with ClientSession() as session:
                async with session.post(
                    endpoint, json=body, timeout=timeout
                ) as resp:
                    if resp.status >= 500:
                        return None, 0, CheckError(
                            "Webgate", f"FlareSolverr {resp.status}"
                        )
                    data = await resp.json()
        except (ClientConnectorError, ServerDisconnectedError) as e:
            return None, 0, CheckError("Webgate unreachable", str(e))
        except asyncio.TimeoutError:
            return None, 0, CheckError("Webgate timeout", endpoint)
        except Exception as e:
            self.logger.debug(e, exc_info=True)
            return None, 0, CheckError("Webgate", str(e))

        if data.get("status") != "ok":
            return None, 0, CheckError("Webgate", data.get("message", "unknown"))

        solution = data.get("solution") or {}
        upstream_status = int(solution.get("status") or 0)
        response_text = solution.get("response") or ""

        # Diagnostic: warn if FlareSolverr returned the CF challenge page
        # itself (challenge not fully solved) rather than the real content.
        # When this happens with sites that have weak presenseStrs/absenceStrs,
        # maigret's default-true presence rule produces false CLAIMED.
        cf_markers = ("Just a moment", "_cf_chl_opt", "cf-mitigated", "challenges.cloudflare.com")
        if response_text and any(m in response_text for m in cf_markers):
            self.logger.warning(
                f"Webgate response from {self.url} still contains CF challenge "
                f"markers (status={upstream_status}, body={len(response_text)}b). "
                f"FlareSolverr likely did not solve the challenge — site checks "
                f"with weak markers may produce false CLAIMED."
            )

        self.logger.info(
            f"Webgate response: url={self.url} status={upstream_status} "
            f"body_len={len(response_text)}"
        )
        return response_text, upstream_status, None

    async def _check_url_rewrite(
        self, module: Dict[str, Any]
    ) -> Tuple[Optional[str], int, Optional[CheckError]]:
        url_template = module.get('url') or ''
        if "{url}" not in url_template:
            return None, 0, CheckError(
                "Webgate", f"module '{module.get('name')}' url has no {{url}} placeholder"
            )
        from urllib.parse import quote_plus

        proxy_url = url_template.format(url=quote_plus(self.url))
        timeout = self.timeout if self.timeout else 30
        try:
            async with ClientSession() as session:
                async with session.get(proxy_url, timeout=timeout) as resp:
                    if resp.status >= 500:
                        return None, 0, CheckError(
                            "Webgate", f"url_rewrite proxy {resp.status}"
                        )
                    body = await resp.text()
        except (ClientConnectorError, ServerDisconnectedError) as e:
            return None, 0, CheckError("Webgate unreachable", str(e))
        except asyncio.TimeoutError:
            return None, 0, CheckError("Webgate timeout", proxy_url)
        except Exception as e:
            self.logger.debug(e, exc_info=True)
            return None, 0, CheckError("Webgate", str(e))

        # url_rewrite mode CANNOT recover the upstream HTTP status.
        # We assume 200 when HTML is returned; status_code/response_url
        # check types will misfire (see docs).
        return body, 200, None


class CheckerMock:
    def __init__(self, *args, **kwargs):
        pass

    def prepare(self, url, headers=None, allow_redirects=True, timeout=0, method='get', payload=None):
        return None

    async def check(self) -> Tuple[Optional[str], int, Optional[CheckError]]:
        await asyncio.sleep(0)
        return '', 0, None

    async def close(self):
        return


def make_protocol_checker(options: QueryOptions, protocol: str):
    checker_factory = options["checkers"][protocol]
    if callable(checker_factory):
        return checker_factory()
    return checker_factory

