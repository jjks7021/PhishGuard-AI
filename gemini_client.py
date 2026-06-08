import httpx
import os
import json
from dotenv import load_dotenv

load_dotenv()
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")


async def analyze_url_with_gemini(url: str, client: httpx.AsyncClient) -> dict:
    if not GEMINI_API_KEY:
        raise ValueError("env 파일에 api키 없음!!")

    api_url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
    )

    # 프롬프트 길면 토큰 아까워서 확 줄임 ㅋ 이렇게해도 에러 잘 안남
    prompt = f"""
당신은 피싱/악성 사이트 분석 전문가입니다.
다음 URL을 분석하고 아래 JSON 형식으로만 출력하세요.

규칙:
1) JSON 외 텍스트 금지.
2) reason은 한국어.
3) 유명 사이트 오타(typosquatting)는 높은 점수(70~100).
4) 도박/성인/피싱/스캠 패턴 발견 시 점수 증가.
5) 안전 사이트는 0~20.
6) suggested_url은 공식 URL 추정 시 https://로 시작, 아니면 null.

분석 URL: {url}

JSON Only:
{{
  "score": 0~100,
  "is_typosquatting": true/false,
  "suggested_url": "https://..." 또는 null,
  "reason": "한국어 설명"
}}
"""

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
        },
    }

    try:
        response = await client.post(api_url, json=payload, timeout=10.0)
        response.raise_for_status()

        data = response.json()
        raw = data["candidates"][0]["content"]["parts"][0]["text"]

        # 제미나이가 가끔 말많아질때 있어서 정규식으로 괄호만 빼오게 처리함
        try:
            result = json.loads(raw)
        except:
            # '{' 로 시작하는 부분부터 '}' 까지 강제로 추출
            import re
            m = re.search(r"\{.*\}", raw, re.DOTALL)
            if not m:
                raise ValueError("JSON 추출 실패")
            result = json.loads(m.group())

        # 추천 url http 안붙이고 주는거 땜에 빡쳐서 수동으로 붙임
        suggested = result.get("suggested_url")
        if isinstance(suggested, str):
            s = suggested.strip()
            if s and not s.lower().startswith(("http://", "https://")):
                s = "https://" + s.lstrip("/")
            result["suggested_url"] = s or None

        return result

    except Exception as e:
        print("Gemini API 오류:", e)
        raise e
