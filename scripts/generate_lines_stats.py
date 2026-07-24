#!/usr/bin/env python3
"""
Organization의 모든 레포에서 Lines of Code 통계를 수집하여 SVG 생성

GraphQL 기반: defaultBranch history의 commit별 additions/deletions 합산.
기존 /stats/code_frequency REST 엔드포인트는 GitHub 측 캐시 무효화 시
영구 202를 반환하는 케이스가 있어 GraphQL로 대체.
"""
import os
import sys
import time
import requests
from datetime import datetime, timezone, timedelta

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
ORG_NAME = "kornukopia-ai"

REST_HEADERS = {
    "Authorization": f"token {GITHUB_TOKEN}",
    "Accept": "application/vnd.github.v3+json"
}
GRAPHQL_HEADERS = {
    "Authorization": f"bearer {GITHUB_TOKEN}",
    "Accept": "application/vnd.github.v3+json"
}
GRAPHQL_URL = "https://api.github.com/graphql"

HISTORY_QUERY = """
query($owner: String!, $repo: String!, $cursor: String) {
  repository(owner: $owner, name: $repo) {
    defaultBranchRef {
      target {
        ... on Commit {
          history(first: 100, after: $cursor) {
            pageInfo { hasNextPage endCursor }
            edges { node { additions deletions } }
          }
        }
      }
    }
  }
}
"""


RETRYABLE_STATUS = {429, 500, 502, 503, 504}
MAX_RETRIES = 5
BACKOFF_BASE_SEC = 2
BACKOFF_CAP_SEC = 60
REQUEST_TIMEOUT_SEC = 30


class GitHubAPIError(Exception):
    """API 호출이 재시도 후에도 복구되지 않은 경우."""


def _is_rate_limited(resp):
    if resp.status_code == 429:
        return True
    return resp.status_code == 403 and (
        resp.headers.get("X-RateLimit-Remaining") == "0"
        or "Retry-After" in resp.headers
    )


def _retry_delay(resp, attempt):
    delay = min(BACKOFF_CAP_SEC, BACKOFF_BASE_SEC * (2 ** attempt))
    retry_after = resp.headers.get("Retry-After")
    if retry_after and retry_after.isdigit():
        return max(delay, int(retry_after))
    if resp.status_code == 403 and resp.headers.get("X-RateLimit-Remaining") == "0":
        reset = resp.headers.get("X-RateLimit-Reset")
        if reset and reset.isdigit():
            return max(0, int(reset) - int(time.time())) + 1
    return delay


def github_request(method, url, **kwargs):
    """5xx·rate limit·네트워크 오류에 지수 backoff로 재시도.

    재시도 후에도 복구되지 않으면 GitHubAPIError를 던진다. 4xx 등 재시도가
    무의미한 응답은 그대로 반환하여 호출부가 상태코드로 판단하게 한다.
    """
    kwargs.setdefault("timeout", REQUEST_TIMEOUT_SEC)
    for attempt in range(MAX_RETRIES + 1):
        try:
            resp = requests.request(method, url, **kwargs)
        except requests.RequestException as exc:
            if attempt >= MAX_RETRIES:
                raise GitHubAPIError(f"{method} {url}: {MAX_RETRIES}회 재시도 후 실패 ({exc})") from exc
            time.sleep(min(BACKOFF_CAP_SEC, BACKOFF_BASE_SEC * (2 ** attempt)))
            continue

        if resp.status_code in RETRYABLE_STATUS or _is_rate_limited(resp):
            if attempt >= MAX_RETRIES:
                raise GitHubAPIError(f"{method} {url}: {MAX_RETRIES}회 재시도 후에도 HTTP {resp.status_code}")
            time.sleep(_retry_delay(resp, attempt))
            continue

        return resp
    raise GitHubAPIError(f"{method} {url}: 재시도 로직 오류")


def get_org_repos():
    """Organization의 모든 레포 가져오기 (private 포함)"""
    repos = []
    page = 1
    while True:
        url = f"https://api.github.com/orgs/{ORG_NAME}/repos?type=all&per_page=100&page={page}"
        resp = github_request("GET", url, headers=REST_HEADERS)
        if resp.status_code != 200:
            raise GitHubAPIError(f"레포 목록 조회 실패 (page {page}): HTTP {resp.status_code}")
        data = resp.json()
        if not data:
            break
        repos.extend(data)
        page += 1
    return repos


def _graphql_history_page(repo_name, cursor):
    """history 한 페이지 조회. RATE_LIMITED 에러는 backoff 재시도, 그 외 실패는 GitHubAPIError."""
    for attempt in range(MAX_RETRIES + 1):
        resp = github_request(
            "POST", GRAPHQL_URL,
            json={"query": HISTORY_QUERY,
                  "variables": {"owner": ORG_NAME, "repo": repo_name, "cursor": cursor}},
            headers=GRAPHQL_HEADERS,
        )
        if resp.status_code != 200:
            raise GitHubAPIError(f"{repo_name} GraphQL HTTP {resp.status_code}")

        payload = resp.json()
        errors = payload.get("errors")
        if errors:
            if any(e.get("type") == "RATE_LIMITED" for e in errors) and attempt < MAX_RETRIES:
                time.sleep(min(BACKOFF_CAP_SEC, BACKOFF_BASE_SEC * (2 ** attempt)))
                continue
            raise GitHubAPIError(f"{repo_name} GraphQL errors: {errors}")
        return payload

    raise GitHubAPIError(f"{repo_name} GraphQL: RATE_LIMITED 재시도 소진")


def get_repo_lines(repo_name):
    """defaultBranch 전체 history에서 additions/deletions 합산"""
    additions = 0
    deletions = 0
    cursor = None

    while True:
        payload = _graphql_history_page(repo_name, cursor)

        ref = payload["data"]["repository"]["defaultBranchRef"]
        if not ref:
            return {"additions": 0, "deletions": 0}

        history = ref["target"]["history"]
        for edge in history["edges"]:
            additions += edge["node"]["additions"]
            deletions += edge["node"]["deletions"]

        if not history["pageInfo"]["hasNextPage"]:
            break
        cursor = history["pageInfo"]["endCursor"]

    return {"additions": additions, "deletions": deletions}


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


def _abort(message):
    print(f"FATAL: {message}")
    print("SVG를 갱신하지 않고 종료합니다 (오염 커밋 방지).")
    sys.exit(1)


def main():
    print(f"Fetching repos for {ORG_NAME}...")
    try:
        repos = get_org_repos()
    except GitHubAPIError as exc:
        _abort(str(exc))

    if not repos:
        _abort("레포 목록이 비어 있습니다.")

    print(f"Found {len(repos)} repos")

    repo_stats = {}

    for repo in repos:
        repo_name = repo["name"]
        print(f"  Processing {repo_name}...")
        try:
            stats = get_repo_lines(repo_name)
        except GitHubAPIError as exc:
            _abort(str(exc))

        if stats["additions"] == 0 and stats["deletions"] == 0:
            continue

        repo_stats[repo_name] = stats
        print(f"    ✓ +{stats['additions']} / -{stats['deletions']}")

    if not repo_stats:
        _abort("데이터가 있는 레포가 없습니다.")

    print(f"\nTotal repos with data: {len(repo_stats)}/{len(repos)}")

    svg = generate_lines_svg(repo_stats)
    with open("lines-of-code.svg", "w") as f:
        f.write(svg)
    print("Generated lines-of-code.svg")


if __name__ == "__main__":
    main()

