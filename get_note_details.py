#!/usr/bin/env python3
"""获取指定笔记的详细内容和评论"""

import asyncio
import json
from pathlib import Path
from playwright.async_api import async_playwright

async def get_note_details(note_ids):
    # 加载 Cookie
    cookie_file = Path("cookies/xiaohongshu.json")
    with open(cookie_file) as f:
        data = json.load(f)
    cookies = data['cookies']
    
    results = {}
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        await context.add_cookies(cookies)
        page = await context.new_page()
        
        for note_id in note_ids:
            note_data = {
                'id': note_id,
                'content': None,
                'comments': []
            }
            
            async def handle_response(response, note_data=note_data):
                url = response.url
                try:
                    if f'/api/sns/web/v1/feed' in url:
                        body = await response.json()
                        if body.get('data', {}).get('items'):
                            for item in body['data']['items']:
                                if item.get('id') == note_id:
                                    note_data['content'] = item
                                    print(f"✓ 获取笔记内容: {note_id}")
                    
                    if '/api/sns/web/v2/comment/page' in url or '/api/sns/web/v1/comment/page' in url:
                        body = await response.json()
                        if body.get('data', {}).get('comments'):
                            note_data['comments'].extend(body['data']['comments'])
                            print(f"✓ 获取 {len(body['data']['comments'])} 条评论")
                except Exception as e:
                    pass
            
            page.on('response', handle_response)
            
            print(f"\n访问笔记: {note_id}")
            await page.goto(f'https://www.xiaohongshu.com/explore/{note_id}')
            await asyncio.sleep(5)
            
            # 滚动加载评论
            for i in range(5):
                await page.evaluate('window.scrollBy(0, 500)')
                await asyncio.sleep(2)
            
            results[note_id] = note_data
            page.remove_listener('response', handle_response)
        
        await browser.close()
    
    return results

async def main():
    note_ids = [
        '61168627000000000102946d',
        '6a1d91ae0000000035022029'
    ]
    
    results = await get_note_details(note_ids)
    
    # 保存结果
    output_file = Path("results/wxhaz_note_details.json")
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    
    print(f"\n{'='*60}")
    print("笔记详情")
    print(f"{'='*60}")
    
    for note_id, data in results.items():
        print(f"\n笔记 ID: {note_id}")
        
        if data['content']:
            content = data['content'].get('note_card', {})
            print(f"标题: {content.get('display_title', content.get('title', 'N/A'))}")
            print(f"描述: {content.get('desc', 'N/A')[:200]}")
            print(f"类型: {content.get('type', 'N/A')}")
            
            interact = content.get('interact_info', {})
            print(f"点赞: {interact.get('liked_count', 'N/A')}")
            print(f"评论: {interact.get('comment_count', 'N/A')}")
            print(f"收藏: {interact.get('collected_count', 'N/A')}")
        
        if data['comments']:
            print(f"\n评论 ({len(data['comments'])} 条):")
            for i, comment in enumerate(data['comments'][:10], 1):
                user = comment.get('user_info', {})
                print(f"  {i}. {user.get('nickname', 'N/A')}: {comment.get('content', 'N/A')[:100]}")
        else:
            print("\n暂无评论")

if __name__ == '__main__':
    asyncio.run(main())
