#!/usr/bin/env python3
"""
Organization의 모든 레포에서 Lines of Code 통계를 수집하여 SVG 생성
"""
import os
import requests
import time
from datetime import datetime, timezone, timedelta

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
ORG_NAME = "kornukopia-ai"

headers = {
    "Authorization": f"token {GITHUB_TOKEN}",
    "Accept": "application/vnd.github.v3+json"
}


def get_org_repos():
    """Organization의 모든 레포 가져오기 (private 포함)"""
    repos = []
    page = 1
    while True:
        url = f"https://api.github.com/orgs/{ORG_NAME}/repos?type=all&per_page=100&page={page}"
        resp = requests.get(url, headers=headers)
        if resp.status_code != 200:
            print(f"Error fetching repos: {resp.status_code}")
            break
        data = resp.json()
        if not data:
            break
        repos.extend(data)
        page += 1
    return repos


def get_code_frequency(repo_name):
    """레포의 주간 코드 변화량 가져오기 - 성공할 때까지 재시도"""
    url = f"https://api.github.com/repos/{ORG_NAME}/{repo_name}/stats/code_frequency"
    
    max_wait = 60  # 최대 60초까지 대기
    waited = 0
    
    while waited < max_wait:
        resp = requests.get(url, headers=headers)
        if resp.status_code == 200:
            data = resp.json()
            if data:  # 빈 배열이 아닌 경우만 반환
                return data
            # 빈 배열이면 계속 대기
        elif resp.status_code == 202:
            pass  # 계산 중 - 계속 대기
        else:
            print(f"    Error for {repo_name}: {resp.status_code}")
            return []
        
        wait_time = 5
        print(f"    Waiting for {repo_name}... ({waited + wait_time}s)")
        time.sleep(wait_time)
        waited += wait_time
    
    print(f"    Timeout for {repo_name}")
    return []


def format_number(n):
    """숫자를 읽기 쉬운 형식으로 변환"""
    if abs(n) >= 1000000:
        return f"{n/1000000:.1f}M"
    elif abs(n) >= 1000:
        return f"{n/1000:.1f}k"
    return str(n)


def calc_diff_boxes(additions, deletions, max_total):
    """diff-box 개수 계산 (lowlighter/metrics 스타일)"""
    total = additions + deletions
    if max_total == 0:
        return 0, 0
    
    # 최대 5개 박스 (3 added + 2 deleted 기준)
    ratio = total / max_total
    add_ratio = additions / total if total > 0 else 0
    del_ratio = deletions / total if total > 0 else 0
    
    # 비율에 따라 박스 개수 결정
    add_boxes = max(1, min(5, round(add_ratio * 5))) if additions > 0 else 0
    del_boxes = max(1, min(5, round(del_ratio * 5))) if deletions > 0 else 0
    
    return add_boxes, del_boxes


def generate_lines_svg(repo_stats, width=480):
    """Lines of Code SVG 생성 (lowlighter/metrics 스타일과 동일)"""
    # 정렬: additions + deletions 합계 기준 내림차순
    sorted_repos = sorted(
        repo_stats.items(),
        key=lambda x: x[1]['additions'] + abs(x[1]['deletions']),
        reverse=True
    )
    
    # 상위 10개만 표시
    top_repos = sorted_repos[:10]
    
    # 총계 계산
    total_added = sum(r['additions'] for _, r in repo_stats.items())
    total_deleted = sum(abs(r['deletions']) for _, r in repo_stats.items())
    
    # max값 계산 (박스 비율용)
    max_total = max((r['additions'] + abs(r['deletions'])) for _, r in top_repos) if top_repos else 1
    
    # 높이 계산
    row_height = 22
    header_height = 40
    footer_height = 30
    height = header_height + len(top_repos) * row_height + footer_height
    
    # 현재 시간 (KST = UTC+9)
    kst = timezone(timedelta(hours=9))
    now = datetime.now(kst).strftime('%d %b %Y, %H:%M:%S')
    
    # 레포별 HTML 생성
    left_rows = []
    right_rows = []
    
    for repo_name, stats in top_repos:
        additions = stats['additions']
        deletions = abs(stats['deletions'])
        add_boxes, del_boxes = calc_diff_boxes(additions, deletions, max_total)
        
        # 왼쪽: 레포 이름
        left_rows.append(f'''                        <div class="field">
                            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="16" height="16">
                                <path fill-rule="evenodd" d="M8 5.5a2.5 2.5 0 100 5 2.5 2.5 0 000-5zM4 8a4 4 0 118 0 4 4 0 01-8 0z"/>
                            </svg>
                            <span class="diff-handle">{ORG_NAME}/{repo_name}</span>
                        </div>''')
        
        # 오른쪽: diff boxes + 숫자
        boxes_html = ''
        for _ in range(add_boxes):
            boxes_html += '<div class="diff-box added"></div>'
        for _ in range(del_boxes):
            boxes_html += '<div class="diff-box deleted"></div>'
        
        right_rows.append(f'''                        <div class="field">
                            {boxes_html}
                            <div class="diff-stats">
                                <span class="added"> +{format_number(additions)}</span>
                                <span class="deleted"> -{format_number(deletions)}</span>
                            </div>
                            <span> </span>
                        </div>''')
    
    left_html = '\n'.join(left_rows)
    right_html = '\n'.join(right_rows)
    
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" class="">
    <defs>
        <style/>
    </defs>
    <style>svg{{font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif,Apple Color Emoji,Segoe UI Emoji;font-size:14px;color:#777}}h1,h2{{margin:8px 0 2px;padding:0;color:#0366d6;font-size:20px;font-weight:700}}h2{{font-weight:400;font-size:16px}}h1 svg,h2 svg{{fill:currentColor}}section>.field{{margin-left:5px;margin-right:5px}}.field{{display:flex;align-items:center;margin-bottom:2px;white-space:nowrap}}.field svg{{margin:0 8px;fill:#959da5;flex-shrink:0}}.row{{display:flex;flex-wrap:wrap}}.row section{{flex:1 1 0}}footer{{margin-top:8px;font-size:10px;font-style:italic;color:#666;text-align:right;display:flex;flex-direction:column;justify-content:flex-end;padding:0 4px}}.diff-handle{{color:#58a6ff;max-width:200px;text-overflow:ellipsis;overflow:hidden}}.diff-box{{display:inline-block;width:8px;height:8px;margin-left:1px;background-color:rgba(110,118,129,.4);border:1px solid rgba(246,240,251,.1)}}.diff-box:first-child{{margin-left:9px}}.diff-box.added{{background-color:#3fb950}}.diff-box.deleted{{background-color:#da3633}}.diff-stats,code,span.code{{font-family:SFMono-Regular,Consolas,Liberation Mono,Menlo,monospace}}.diff-stats{{margin-left:4px;font-weight:700;font-size:12px;white-space:nowrap}}.added{{color:#3fb950}}.deleted{{color:#da3633}}code,span.code{{background-color:#7777771f;padding:1px 5px;font-size:80%;border-radius:6px;color:#777}}code{{display:inline-block}}span.code{{margin:0 4px -3px}}#metrics-end{{width:100%}}</style>
    <style/>
    <foreignObject x="0" y="0" width="100%" height="100%">
        <div xmlns="http://www.w3.org/1999/xhtml" xmlns:xlink="http://www.w3.org/1999/xlink" class="items-wrapper">
            <section>
                <h2 class="field">
                    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="16" height="16">
                        <path fill-rule="evenodd" d="M2.75 1.5a.25.25 0 00-.25.25v12.5c0 .138.112.25.25.25h10.5a.25.25 0 00.25-.25V4.664a.25.25 0 00-.073-.177l-2.914-2.914a.25.25 0 00-.177-.073H2.75zM1 1.75C1 .784 1.784 0 2.75 0h7.586c.464 0 .909.184 1.237.513l2.914 2.914c.329.328.513.773.513 1.237v9.586A1.75 1.75 0 0113.25 16H2.75A1.75 1.75 0 011 14.25V1.75zm7 1.5a.75.75 0 01.75.75v1.5h1.5a.75.75 0 010 1.5h-1.5v1.5a.75.75 0 01-1.5 0V7h-1.5a.75.75 0 010-1.5h1.5V4A.75.75 0 018 3.25zm-3 8a.75.75 0 01.75-.75h4.5a.75.75 0 010 1.5h-4.5a.75.75 0 01-.75-.75z"/>
                    </svg>
                    Lines of code pushed
                </h2>
                <div class="row">
                    <section>
{left_html}
                    </section>
                    <section>
{right_html}
                    </section>
                </div>
            </section>
            <footer>
                <span>Total: +{format_number(total_added)} / -{format_number(total_deleted)} · {len(repo_stats)} repositories · {now} (Asia/Seoul)</span>
            </footer>
        </div>
        <div xmlns="http://www.w3.org/1999/xhtml" id="metrics-end"></div>
    </foreignObject>
</svg>'''
    
    return svg


def main():
    print(f"Fetching repos for {ORG_NAME}...")
    repos = get_org_repos()
    print(f"Found {len(repos)} repos")
    
    repo_names = [repo["name"] for repo in repos]
    repo_stats = {}
    
    # 모든 레포 데이터 수집 (최대 3라운드)
    for round_num in range(3):
        pending = [name for name in repo_names if name not in repo_stats]
        if not pending:
            break
        
        print(f"\n=== Round {round_num + 1}: {len(pending)} repos remaining ===")
        
        for repo_name in pending:
            print(f"  Processing {repo_name}...")
            
            code_freq = get_code_frequency(repo_name)
            
            if code_freq:
                total_additions = sum(week[1] for week in code_freq if len(week) >= 3)
                total_deletions = sum(week[2] for week in code_freq if len(week) >= 3)
                
                if total_additions > 0 or total_deletions < 0:
                    repo_stats[repo_name] = {
                        'additions': total_additions,
                        'deletions': total_deletions
                    }
                    print(f"    ✓ +{total_additions} / {total_deletions}")
            
            time.sleep(0.5)
    
    # 결과 확인
    missing = [name for name in repo_names if name not in repo_stats]
    print(f"\nTotal repos with data: {len(repo_stats)}/{len(repo_names)}")
    if missing:
        print(f"Missing: {missing}")
    
    # SVG 생성
    svg = generate_lines_svg(repo_stats)
    with open("lines-of-code.svg", "w") as f:
        f.write(svg)
    print("Generated lines-of-code.svg")


if __name__ == "__main__":
    main()

