from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import httpx

from schemas import EvaluateRequest, DecisionResponse, ReportRequest, ReportResponse
import service
import decision_engine


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with httpx.AsyncClient() as client:
        app.state.http_client = client
        yield


app = FastAPI(title="PhishGuard-AI Unified API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_http_client(request: Request) -> httpx.AsyncClient:
    return request.app.state.http_client


@app.get("/health")
async def health():
    return {"status": "ok"}


# URL 평가 라우터 (DB확인하고 GSB, 제미나이 순으로 돌림)
@app.post("/api/evaluate", response_model=DecisionResponse)
async def evaluate_url(
    body: EvaluateRequest,
    client: httpx.AsyncClient = Depends(get_http_client),
):
    try:
        url_str = str(body.url).strip()
        client_id = (body.client_id or "").strip() if hasattr(body, "client_id") else ""
        decision_data = await decision_engine.get_decision(client_id, client, url_str)
        return DecisionResponse(**decision_data)
    except Exception as e:
        print("[/api/evaluate] error:", e)
        raise HTTPException(status_code=500, detail="Internal Server Error")


# 사용자 신고 API
@app.post("/api/report", response_model=ReportResponse)
async def report_url(body: ReportRequest):
    # 하드코딩했던 토큰 빼고 프론트에서 넘어온거 쓰게 고침
    ok = service.report_url(body.user_token, str(body.url))
    if not ok:
        raise HTTPException(status_code=500, detail="Report failed")
    return ReportResponse(message="신고가 접수되었습니다.", report_id=None)


# 차단 수동설정
@app.post("/api/override")
async def override_api(body: dict):
    client_id = body.get("client_id")
    url = body.get("url")
    decision = body.get("decision")
    if not client_id or not url or decision is None:
        raise HTTPException(status_code=400, detail="invalid body")
    ok = service.override_url(client_id, url, int(decision))
    return {"success": ok}


# 수동차단 푸는거
@app.post("/api/remove-override")
async def remove_override_api(body: dict):
    client_id = body.get("client_id")
    url = body.get("url")
    if not client_id or not url:
        raise HTTPException(status_code=400, detail="invalid body")
    ok = service.remove_override_url(client_id, url)
    return {"success": ok}


# 내가 차단한 리스트 가져오기
@app.post("/api/my-blocked-urls")
async def my_blocked_urls(body: dict):
    client_id = body.get("client_id")
    if not client_id:
        raise HTTPException(status_code=400, detail="invalid body")
    urls = service.get_user_blocked_urls(client_id)
    return {"urls": urls}


# 프론트(blocked.js)에서 쓰려고 만든 정보조회 API
# ai_cache 디비 뒤져서 나옴
@app.post("/api/global-info")
async def get_global_info(body: dict):
    url = body.get("url")
    if not url:
        raise HTTPException(status_code=400, detail="url required")

    # ai_cache에서 캐시된 정보 조회 (최대 1년)
    cache = service.get_ai_cache(url, max_age_days=365)
    if not cache:
        return {
            "ai_reason": None,
            "ai_score": None,
            "official_url": None,
        }

    return {
        "ai_reason": cache.get("ai_reason"),
        "ai_score": cache.get("ai_score"),
        "official_url": cache.get("suggested_official_url"),
    }
