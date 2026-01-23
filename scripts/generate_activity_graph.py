#!/usr/bin/env python3
"""
Organization의 commit activity를 가져와서 활동 그래프 SVG 생성
"""
import os
import requests
from datetime import datetime, timedelta, timezone
from collections import defaultdict

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


def get_commit_activity(repo_name):
    """레포의 주간 commit activity 가져오기 (최근 52주)"""
    url = f"https://api.github.com/repos/{ORG_NAME}/{repo_name}/stats/commit_activity"
    resp = requests.get(url, headers=headers)
    if resp.status_code == 200:
        return resp.json()
    return []


def get_daily_commits(repo_name, days=90):
    """레포의 일별 커밋 수 가져오기 (UTC 기준)"""
    # UTC 기준으로 시간 계산
    now_utc = datetime.now(timezone.utc)
    since = (now_utc - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    commits_by_day = defaultdict(int)
    page = 1
    
    while True:
        url = f"https://api.github.com/repos/{ORG_NAME}/{repo_name}/commits?since={since}&per_page=100&page={page}"
        resp = requests.get(url, headers=headers)
        if resp.status_code != 200:
            break
        data = resp.json()
        if not data:
            break
        
        for commit in data:
            # UTC 날짜 그대로 사용 (ISO 8601 형식에서 날짜 부분만 추출)
            date_str = commit["commit"]["author"]["date"][:10]
            commits_by_day[date_str] += 1
        
        if len(data) < 100:
            break
        page += 1
    
    return commits_by_day


def generate_full_activity_svg(daily_data, width=400, height=120):
    """전체 활동 그래프 SVG 생성 (GitHub 스타일)"""
    # 최근 90일 데이터 정리 (UTC 기준)
    today_utc = datetime.now(timezone.utc).date()
    dates = [(today_utc - timedelta(days=i)).isoformat() for i in range(89, -1, -1)]
    values = [daily_data.get(d, 0) for d in dates]
    
    max_val = max(values) if values and max(values) > 0 else 1
    
    # SVG 생성
    svg_parts = [f'''<svg width="{width}" height="{height}" xmlns="http://www.w3.org/2000/svg">
  <style>
    .title {{ font: bold 14px -apple-system, BlinkMacSystemFont, sans-serif; fill: #c9d1d9; }}
    .subtitle {{ font: 11px -apple-system, BlinkMacSystemFont, sans-serif; fill: #8b949e; }}
    .axis {{ font: 10px -apple-system, BlinkMacSystemFont, sans-serif; fill: #8b949e; }}
  </style>
  <rect width="100%" height="100%" fill="#0d1117" rx="6"/>
  <text x="16" y="28" class="title">Commit Activity</text>
  <text x="16" y="44" class="subtitle">Last 90 days</text>
''']
    
    # 그래프 영역
    graph_x = 16
    graph_y = 55
    graph_width = width - 32
    graph_height = height - 75
    
    # 그리드 라인
    for i in range(5):
        y = graph_y + (graph_height / 4) * i
        svg_parts.append(f'  <line x1="{graph_x}" y1="{y}" x2="{graph_x + graph_width}" y2="{y}" stroke="#21262d" stroke-width="1"/>')
    
    # 데이터 포인트 생성
    if values:
        step = graph_width / (len(values) - 1) if len(values) > 1 else graph_width
        points = []
        
        for i, val in enumerate(values):
            x = graph_x + i * step
            y = graph_y + graph_height - (val / max_val * graph_height) if max_val > 0 else graph_y + graph_height
            points.append(f"{x:.1f},{y:.1f}")
        
        points_str = " ".join(points)
        
        # 채우기
        fill_points = f"{graph_x},{graph_y + graph_height} " + points_str + f" {graph_x + graph_width},{graph_y + graph_height}"
        svg_parts.append(f'''  <defs>
    <linearGradient id="fillGrad" x1="0%" y1="0%" x2="0%" y2="100%">
      <stop offset="0%" style="stop-color:#3fb950;stop-opacity:0.3"/>
      <stop offset="100%" style="stop-color:#3fb950;stop-opacity:0"/>
    </linearGradient>
  </defs>
  <polygon fill="url(#fillGrad)" points="{fill_points}"/>
  <polyline fill="none" stroke="#3fb950" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" points="{points_str}"/>''')
    
    # 통계
    total = sum(values)
    avg = total / len(values) if values else 0
    svg_parts.append(f'  <text x="{width - 16}" y="28" class="subtitle" text-anchor="end">{total} commits</text>')
    svg_parts.append(f'  <text x="{width - 16}" y="44" class="subtitle" text-anchor="end">avg {avg:.1f}/day</text>')
    
    svg_parts.append('</svg>')
    
    return "\n".join(svg_parts)


def main():
    print(f"Fetching repos for {ORG_NAME}...")
    repos = get_org_repos()
    print(f"Found {len(repos)} repos")
    
    # 모든 레포의 일별 커밋 합산
    all_daily_commits = defaultdict(int)
    
    for repo in repos:
        repo_name = repo["name"]
        print(f"  Processing {repo_name}...")
        daily = get_daily_commits(repo_name, days=90)
        for date, count in daily.items():
            all_daily_commits[date] += count
    
    print(f"Total commit days: {len(all_daily_commits)}")
    
    # 전체 활동 그래프 SVG
    full_svg = generate_full_activity_svg(all_daily_commits)
    with open("commit-activity.svg", "w") as f:
        f.write(full_svg)
    print("Generated commit-activity.svg")


if __name__ == "__main__":
    main()

