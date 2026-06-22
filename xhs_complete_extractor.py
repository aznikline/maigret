#!/usr/bin/env python3
"""
小红书完整数据提取工具
使用 Playwright 拦截 API 响应，提取用户笔记、评论等完整数据
"""

import asyncio
import json
import re
from pathlib import Path
from playwright.async_api import async_playwright


async def extract_wxhaz_complete():
    """提取 wxhaz 用户的完整数据"""
    
    # 加载 Cookie
    cookie_file = Path(__file__).parent / "cookies" / "xiaohongshu.json"
    with open(cookie_file) as f:
        data = json.load(f)
    cookies = data['cookies']
    
    results = {
        'users': [],
        'notes': [],
        'comments': {},
        'search_results': []
    }
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        await context.add_cookies(cookies)
        page = await context.new_page()
        
        # 拦截所有 API 响应
        async def handle_response(response):
            url = response.url
            try:
                if 'edith.xiaohongshu.com/api' in url:
                    body = await response.json()
                    
                    # 搜索用户
                    if '/v1/search/onebox' in url and body.get('data', {}).get('onebox_list'):
                        for item in body['data']['onebox_list']:
                            if 'user_one_box' in item:
                                results['users'].append(item['user_one_box'])
                                print(f"✓ 找到用户: {item['user_one_box'].get('title', 'N/A')}")
                    
                    # 搜索结果
                    elif '/v1/search/notes' in url and body.get('data', {}).get('items'):
                        for item in body['data']['items']:
                            results['search_results'].append(item)
                        print(f"✓ 获取 {len(body['data']['items'])} 条搜索结果")
                    
                    # 笔记详情
                    elif '/v2/note/' in url or '/v1/note/' in url:
                        if body.get('data'):
                            note_id = body['data'].get('id') or body['data'].get('note_id')
                            if note_id:
                                results['notes'].append(body['data'])
                                print(f"✓ 获取笔记详情: {note_id}")
                    
                    # 评论
                    elif '/comment/' in url and body.get('data', {}).get('comments'):
                        note_id_match = re.search(r'/note/([^/]+)/', url)
                        if note_id_match:
                            note_id = note_id_match.group(1)
                            if note_id not in results['comments']:
                                results['comments'][note_id] = []
                            results['comments'][note_id].extend(body['data']['comments'])
                            print(f"✓ 获取 {len(body['data']['comments'])} 条评论 (笔记: {note_id})")
                    
                    # 用户笔记列表
                    elif '/v1/user/posted' in url or '/v1/user/notes' in url:
                        if body.get('data', {}).get('notes'):
                            for note in body['data']['notes']:
                                results['notes'].append(note)
                            print(f"✓ 获取 {len(body['data']['notes'])} 条用户笔记")
                            
            except Exception as e:
                pass
        
        page.on('response', handle_response)
        
        # 1. 搜索 wxhaz 用户
        print("\n[1/4] 搜索 wxhaz 用户...")
        await page.goto('https://www.xiaohongshu.com/search_result?keyword=wxhaz&type=user')
        await asyncio.sleep(5)
        
        # 2. 搜索包含 wxhaz 的笔记
        print("\n[2/4] 搜索 wxhaz 相关笔记...")
        await page.goto('https://www.xiaohongshu.com/search_result?keyword=wxhaz&type=notes')
        await asyncio.sleep(5)
        
        # 3. 访问找到的用户主页
        print("\n[3/4] 访问用户主页...")
        for user in results['users']:
            user_id = user.get('id')
            if user_id:
                print(f"  访问用户: {user.get('title')} ({user_id})")
                await page.goto(f'https://www.xiaohongshu.com/user/profile/{user_id}')
                await asyncio.sleep(3)
                
                # 滚动加载更多笔记
                for _ in range(3):
                    await page.evaluate('window.scrollBy(0, 500)')
                    await asyncio.sleep(1)
        
        # 4. 访问每个笔记获取评论
        print("\n[4/4] 获取笔记评论...")
        for note in results['notes'][:10]:  # 限制前10个笔记
            note_id = note.get('id') or note.get('note_id')
            if note_id:
                print(f"  访问笔记: {note_id}")
                await page.goto(f'https://www.xiaohongshu.com/explore/{note_id}')
                await asyncio.sleep(3)
                
                # 滚动加载更多评论
                for _ in range(5):
                    await page.evaluate('window.scrollBy(0, 300)')
                    await asyncio.sleep(1)
        
        await browser.close()
    
    # 保存结果
    output_dir = Path(__file__).parent / "results"
    output_dir.mkdir(exist_ok=True)
    
    output_file = output_dir / "wxhaz_complete.json"
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    
    print(f"\n✓ 数据已保存到: {output_file}")
    
    # 生成摘要
    print(f"\n{'='*60}")
    print("数据摘要")
    print(f"{'='*60}")
    print(f"用户数: {len(results['users'])}")
    print(f"笔记数: {len(results['notes'])}")
    print(f"搜索结果: {len(results['search_results'])}")
    print(f"评论总数: {sum(len(c) for c in results['comments'].values())}")
    
    # 显示用户信息
    if results['users']:
        print(f"\n{'='*60}")
        print("用户信息")
        print(f"{'='*60}")
        for user in results['users']:
            print(f"\n昵称: {user.get('title', 'N/A')}")
            print(f"ID: {user.get('id')}")
            print(f"小红书号: {user.get('red_id', 'N/A')}")
            print(f"粉丝: {user.get('fans', 'N/A')}")
            print(f"笔记数: {user.get('note_count', 'N/A')}")
            print(f"简介: {user.get('desc', 'N/A')}")
    
    # 显示笔记信息
    if results['notes']:
        print(f"\n{'='*60}")
        print("笔记列表")
        print(f"{'='*60}")
        for i, note in enumerate(results['notes'][:10], 1):
            title = note.get('display_title', note.get('title', 'N/A'))
            note_id = note.get('id', note.get('note_id', 'N/A'))
            user = note.get('user', {})
            interact = note.get('interact_info', {})
            
            print(f"\n[{i}] {title}")
            print(f"    ID: {note_id}")
            print(f"    作者: {user.get('nickname', 'N/A')}")
            print(f"    点赞: {interact.get('liked_count', 'N/A')}")
            print(f"    评论: {interact.get('comment_count', 'N/A')}")
            print(f"    收藏: {interact.get('collected_count', 'N/A')}")
            
            # 显示评论
            if note_id in results['comments']:
                comments = results['comments'][note_id]
                if comments:
                    print(f"    评论内容:")
                    for comment in comments[:5]:
                        user_info = comment.get('user_info', {})
                        content = comment.get('content', '')
                        print(f"      - {user_info.get('nickname', 'N/A')}: {content[:50]}")
    
    return results


if __name__ == '__main__':
    asyncio.run(extract_wxhaz_complete())
