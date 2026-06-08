import httpx
import os
from dotenv import load_dotenv
from typing import Optional, Tuple, Dict

# .env 로드 (API 키 준비)
load_dotenv()
GOOGLE_API_KEY = os.environ.get("GOOGLE_SAFE_BROWSING_API_KEY")

# 엔진쪽이랑 같이 쓸 상수들
GSB_STATUS_DANGEROUS = "DANGEROUS"
GSB_STATUS_SAFE = "SAFE"

async def check_safe_browsing(
    url: str, client: httpx.AsyncClient
) -> Tuple[str, Optional[Dict]]:
    # GSB api 쏴서 위험/안전 상태 리턴하는 함수
    if not GOOGLE_API_KEY:
        raise ValueError("GOOGLE_SAFE_BROWSING_API_KEY가 .env에 설정되지 않았습니다.")

    api_url = f"https://safebrowsing.googleapis.com/v4/threatMatches:find?key={GOOGLE_API_KEY}"
    payload = {
        "client": {"clientId": "phishguard-ai", "clientVersion": "1.0.0"},
        "threatInfo": {
            "threatTypes": ["MALWARE", "SOCIAL_ENGINEERING", "UNWANTED_SOFTWARE"],
            "platformTypes": ["ANY_PLATFORM"],
            "threatEntryTypes": ["URL"],
            "threatEntries": [{"url": url}]
        }
    }
    
    try:
        response = await client.post(api_url, json=payload, timeout=5.0)
        response.raise_for_status() 
        data = response.json()
        
        if data.get('matches'):
            return (GSB_STATUS_DANGEROUS, data) # '위험' 상태와 원본 데이터 반환
        else:
            return (GSB_STATUS_SAFE, None) # '안전' 상태 반환

    except httpx.RequestError as e:
        print(f"Google Safe Browsing API 오류: {e}")
        # 여기서 에러잡지말고 호출한쪽에서 처리하라고 던짐
        raise e