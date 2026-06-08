from datetime import datetime
import hashlib
from urllib.parse import urlparse, urlunparse, parse_qs, urlencode
from typing import List, Optional

from db import get_connection


# URL 파싱 공통부분

def _normalize_common_base(url: str):
    """
    공통으로 쓰는 기본 파싱: scheme/host/path 정리
    """
    p = urlparse(url)

    scheme = (p.scheme or "https").lower()
    host = p.netloc.lower()

    path = p.path or "/"
    if path != "/":
        path = path.rstrip("/")

    query = p.query or ""

    return p, scheme, host, path, query


def _remove_tracking_params(raw_query: str) -> str:
    """
    utm_*, gclid 같은 트래킹 파라미터 제거
    """
    if not raw_query:
        return ""

    raw_params = parse_qs(raw_query, keep_blank_values=True)

    TRACKING_KEYS = {
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "gclid",
        "fbclid",
        "igshid",
        "mc_eid",
        "mc_cid",
        "ref",
        "ref_src",
    }

    clean_params = {}
    for key, values in raw_params.items():
        kl = key.lower()
        if kl.startswith("utm_"):
            continue
        if kl in TRACKING_KEYS:
            continue
        clean_params[key] = values

    return urlencode(clean_params, doseq=True) if clean_params else ""


# 수동차단용 url 정리함수 (검색어 파라미터만 살리기)
def normalize_url_for_override(url: str) -> str:
    p, scheme, host, path, query = _normalize_common_base(url)

    # 1) 네이버 검색: query 파라미터만 유지
    if host == "search.naver.com" and path == "/search.naver":
        raw_params = parse_qs(query, keep_blank_values=True)
        clean_params = {}
        if "query" in raw_params:
            clean_params["query"] = raw_params["query"]
        # 필요하면 where 등 추가 가능
        clean_query = urlencode(clean_params, doseq=True) if clean_params else ""
        return urlunparse((scheme, host, path, "", clean_query, ""))

    # 2) 네이버 블로그: 글 주소만으로 충분 → 쿼리 전체 제거
    if host in ("blog.naver.com", "m.blog.naver.com"):
        return urlunparse((scheme, host, path, "", "", ""))

    # 3) 구글 검색: q 파라미터만 유지 (검색어별로 구분)
    if host in ("www.google.com", "google.com") and path == "/search":
        raw_params = parse_qs(query, keep_blank_values=True)
        clean_params = {}
        if "q" in raw_params:
            clean_params["q"] = raw_params["q"]
        clean_query = urlencode(clean_params, doseq=True) if clean_params else ""
        return urlunparse((scheme, host, path, "", clean_query, ""))

    # 4) 그 외: 트래킹 파라미터만 제거하고 나머지 쿼리는 유지
    clean_query = _remove_tracking_params(query)
    return urlunparse((scheme, host, path, "", clean_query, ""))


# 캐싱할때 쓸 url 정리. 블로그같은건 뒤에 쿼리 다 뗌
def normalize_url_for_cache(url: str) -> str:
    p, scheme, host, path, query = _normalize_common_base(url)

    # 1) 네이버 검색: query만 기준으로 캐시
    if host == "search.naver.com" and path == "/search.naver":
        raw_params = parse_qs(query, keep_blank_values=True)
        clean_params = {}
        if "query" in raw_params:
            clean_params["query"] = raw_params["query"]
        clean_query = urlencode(clean_params, doseq=True) if clean_params else ""
        return urlunparse((scheme, host, path, "", clean_query, ""))

    # 2) 구글 검색: q 기반으로 나눔
    if host in ("www.google.com", "google.com") and path == "/search":
        raw_params = parse_qs(query, keep_blank_values=True)
        clean_params = {}
        if "q" in raw_params:
            clean_params["q"] = raw_params["q"]
        clean_query = urlencode(clean_params, doseq=True) if clean_params else ""
        return urlunparse((scheme, host, path, "", clean_query, ""))

    # 3) 네이버 블로그는 캐시에서도 굳이 쿼리를 유지할 필요가 거의 없음 → 글 단위
    if host in ("blog.naver.com", "m.blog.naver.com"):
        return urlunparse((scheme, host, path, "", "", ""))

    # 4) 나머지는 트래킹만 제거하고 쿼리 유지 (검색어/필터별 캐시 분리)
    clean_query = _remove_tracking_params(query)
    return urlunparse((scheme, host, path, "", clean_query, ""))


# 예전코드 안터지게 냅둠
def normalize_url(url: str) -> str:
    return normalize_url_for_override(url)


# SHA256 해시
def _make_url_hash(normalized_url: str) -> bytes:
    return hashlib.sha256(normalized_url.encode("utf-8")).digest()


# --------------------------------------------------------
# user_id 생성
# --------------------------------------------------------
def _get_or_create_user_id(client_id: str) -> int:
    conn = get_connection()
    if not conn:
        raise Exception("[_get_or_create_user_id] DB 연결 실패")

    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT id FROM users WHERE external_id=%s LIMIT 1",
                (client_id,),
            )
            row = cursor.fetchone()
            if row:
                return row["id"]

            cursor.execute(
                "INSERT INTO users (display_name, external_id, created_at) VALUES (%s,%s,%s)",
                ("", client_id, datetime.now()),
            )
            conn.commit()
            return conn.insert_id() if hasattr(conn, "insert_id") else cursor.lastrowid
    finally:
        conn.close()


# 모두에게 적용되는 전역차단 등록
def add_global_block(
    url: str,
    ai_reason: Optional[str] = None,
    suggested_url: Optional[str] = None,
) -> bool:
    conn = get_connection()
    if not conn:
        print("[add_global_block] DB 연결 실패")
        return False

    # DB에는 짧은 버전 (override 기준) 저장
    normalized_db = normalize_url_for_override(url)
    # 해시는 캐시 기준(검색어 포함)으로 생성
    normalized_for_hash = normalize_url_for_cache(url)
    url_hash = _make_url_hash(normalized_for_hash)

    try:
        with conn.cursor() as cursor:
            sql = """
            INSERT INTO phishing_sites (
                normalized_url,
                url_hash,
                is_blocked,
                ai_reason,
                suggested_official_url,
                created_at
            )
            VALUES (%s, %s, 1, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                is_blocked = 1,
                ai_reason = VALUES(ai_reason),
                suggested_official_url = VALUES(suggested_official_url),
                created_at = VALUES(created_at)
            """
            cursor.execute(
                sql,
                (normalized_db, url_hash, ai_reason, suggested_url, datetime.now()),
            )
        conn.commit()
        return True
    except Exception as e:
        print("[add_global_block] 에러:", e)
        return False
    finally:
        conn.close()


# 디비 뒤져서 차단할지 말지 정하는 함수 (2:전역, 1:개인, 0:통과)
def check_url(client_id: str, url: str) -> int:
    # 두 가지 기준으로 따로 정규화
    normalized_override = normalize_url_for_override(url)  # 개인 오버라이드용
    url_hash_override = _make_url_hash(normalized_override)

    normalized_cache = normalize_url_for_cache(url)        # 전역/캐시용
    url_hash_cache = _make_url_hash(normalized_cache)

    # user 생성 또는 조회
    try:
        user_id = _get_or_create_user_id(client_id)
    except Exception:
        return 0

    conn = get_connection()
    if not conn:
        print("[check_url] DB 연결 실패")
        return 0

    try:
        with conn.cursor() as cursor:
            # 0) 전역 도메인 차단
            host = urlparse(normalized_cache).netloc.lower()

            cursor.execute(
                """
                SELECT 1
                FROM phishing_sites
                WHERE is_domain_block = 1
                  AND (
                        domain = %s
                     OR %s LIKE CONCAT('%%.', domain)
                  )
                LIMIT 1
                """,
                (host, host),
            )
            row = cursor.fetchone()
            if row:
                return 2

            # 1) 전역 URL 차단 (AI/GSB)
            cursor.execute(
                "SELECT is_blocked FROM phishing_sites WHERE url_hash=%s LIMIT 1",
                (url_hash_cache,),
            )
            row = cursor.fetchone()
            if row and int(row["is_blocked"]) == 1:
                return 2

            # 2) 개인 오버라이드
            cursor.execute(
                """
                SELECT decision
                FROM user_url_overrides
                WHERE user_id=%s AND url_hash=%s
                LIMIT 1
                """,
                (user_id, url_hash_override),
            )
            row = cursor.fetchone()
            if row is not None:
                return int(row["decision"])

            # 3) 기본 허용
            return 0

    except Exception as e:
        print("[check_url] 에러:", e)
        return 0
    finally:
        conn.close()


# --------------------------------------------------------
# 신고 (사용자 → 수동 신고)
# --------------------------------------------------------
def report_url(client_id: str, url: str) -> bool:
    try:
        user_id = _get_or_create_user_id(client_id)
    except Exception:
        return False

    conn = get_connection()
    if not conn:
        print("[report_url] DB 연결 실패")
        return False

    normalized_url = normalize_url_for_override(url)
    url_hash = _make_url_hash(normalized_url)

    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO reported_urls (reporter_user_id, normalized_url, url_hash, created_at)
                VALUES (%s, %s, %s, %s)
                """,
                (user_id, normalized_url, url_hash, datetime.now()),
            )
        conn.commit()
        return True
    except Exception as e:
        print("[report_url] 에러:", e)
        return False
    finally:
        conn.close()


# --------------------------------------------------------
# 개인 차단
# --------------------------------------------------------
def override_url(client_id: str, normalized_url: str, decision: int) -> bool:
    try:
        user_id = _get_or_create_user_id(client_id)
    except Exception:
        return False

    conn = get_connection()
    if not conn:
        print("[override_url] DB 연결 실패")
        return False

    normalized_url = normalize_url_for_override(normalized_url)
    url_hash = _make_url_hash(normalized_url)

    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO user_url_overrides (user_id, normalized_url, url_hash, decision, created_at)
                VALUES (%s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    decision = VALUES(decision),
                    normalized_url = VALUES(normalized_url),
                    created_at = VALUES(created_at)
                """,
                (user_id, normalized_url, url_hash, decision, datetime.now()),
            )
        conn.commit()
        return True
    except Exception as e:
        print("[override_url] 에러:", e)
        return False
    finally:
        conn.close()


# --------------------------------------------------------
# 개인 차단 해제
# --------------------------------------------------------
def remove_override_url(client_id: str, normalized_url: str) -> bool:
    try:
        user_id = _get_or_create_user_id(client_id)
    except Exception:
        return False

    conn = get_connection()
    if not conn:
        print("[remove_override_url] DB 연결 실패")
        return False

    normalized_url = normalize_url_for_override(normalized_url)
    url_hash = _make_url_hash(normalized_url)

    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "DELETE FROM user_url_overrides WHERE user_id=%s AND url_hash=%s",
                (user_id, url_hash),
            )
        conn.commit()
        return cursor.rowcount > 0
    except Exception as e:
        print("[remove_override_url] 에러:", e)
        return False
    finally:
        conn.close()


# --------------------------------------------------------
# 개인 차단 목록
# --------------------------------------------------------
def get_user_blocked_urls(client_id: str) -> List[str]:
    try:
        user_id = _get_or_create_user_id(client_id)
    except Exception:
        return []

    conn = get_connection()
    if not conn:
        print("[get_user_blocked_urls] DB 연결 실패")
        return []

    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT normalized_url
                FROM user_url_overrides
                WHERE user_id=%s AND decision=1
                ORDER BY created_at DESC
                """,
                (user_id,),
            )
            rows = cursor.fetchall() or []
            return [row["normalized_url"] for row in rows]
    except Exception as e:
        print("[get_user_blocked_urls] 에러:", e)
        return []
    finally:
        conn.close()


# --------------------------------------------------------
# AI / GSB 캐시 조회
# --------------------------------------------------------
def get_ai_cache(url: str, max_age_days: int = 30) -> Optional[dict]:
    conn = get_connection()
    if not conn:
        print("[get_ai_cache] DB 연결 실패")
        return None

    normalized_for_hash = normalize_url_for_cache(url)
    url_hash = _make_url_hash(normalized_for_hash)

    try:
        with conn.cursor() as cursor:
            sql = """
            SELECT
                gsb_status,
                ai_score,
                ai_reason,
                suggested_official_url,
                updated_at
            FROM ai_cache
            WHERE url_hash = %s
              AND updated_at >= (NOW() - INTERVAL %s DAY)
            LIMIT 1
            """
            cursor.execute(sql, (url_hash, max_age_days))
            row = cursor.fetchone()
            if not row:
                return None

            return {
                "gsb_status": row["gsb_status"],
                "ai_score": row["ai_score"],
                "ai_reason": row["ai_reason"],
                "suggested_official_url": row["suggested_official_url"],
            }
    except Exception as e:
        print("[get_ai_cache] 에러:", e)
        return None
    finally:
        conn.close()


# --------------------------------------------------------
# AI / GSB 캐시 저장 or 갱신
# --------------------------------------------------------
def upsert_ai_cache(
    url: str,
    gsb_status: Optional[int] = None,
    ai_score: Optional[int] = None,
    ai_reason: Optional[str] = None,
    suggested_url: Optional[str] = None,
) -> bool:
    conn = get_connection()
    if not conn:
        print("[upsert_ai_cache] DB 연결 실패")
        return False

    normalized_db = normalize_url_for_override(url)
    normalized_for_hash = normalize_url_for_cache(url)
    url_hash = _make_url_hash(normalized_for_hash)
    now = datetime.now()

    # gsb_status 문자열 들어와도 안전하게 숫자로 변환
    gsb_value: Optional[int]
    if isinstance(gsb_status, str):
        upper = gsb_status.upper()
        if upper == "SAFE":
            gsb_value = 0
        elif upper in ("DANGEROUS", "UNSAFE", "MALICIOUS"):
            gsb_value = 1
        else:
            gsb_value = None
    else:
        gsb_value = gsb_status

    try:
        with conn.cursor() as cursor:
            sql = """
            INSERT INTO ai_cache (
                normalized_url,
                url_hash,
                gsb_status,
                ai_score,
                ai_reason,
                suggested_official_url,
                updated_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                gsb_status = VALUES(gsb_status),
                ai_score = VALUES(ai_score),
                ai_reason = VALUES(ai_reason),
                suggested_official_url = VALUES(suggested_official_url),
                updated_at = VALUES(updated_at)
            """
            cursor.execute(
                sql,
                (normalized_db, url_hash, gsb_value, ai_score, ai_reason, suggested_url, now),
            )
        conn.commit()
        return True
    except Exception as e:
        print("[upsert_ai_cache] 에러:", e)
        return False
    finally:
        conn.close()
