import httpx
import asyncio
import json
import uuid

async def test_analyze():
    url = "http://127.0.0.1:8001/api/v1/analyze"
    
    # User's payload with generated valid UUIDs for format correctness
    payload = {
        "session_uuid": str(uuid.uuid4()),
        "message_uuid": str(uuid.uuid4()),
        "text": "Assalomu alekum, meni tuman hokimiga murojatim bor iltimos, menga tuman hokimi kerak.",
        "settings": {
            "model": "gemini-2.0-flash",
            "temperature": 0.2,
            "max_tokens": 500
        }
    }
    
    print(f"Sending POST to {url}...")
    print(json.dumps(payload, indent=2))
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=payload, timeout=20.0)
            print(f"\nStatus Code: {response.status_code}")
            print(f"Response: {response.json()}")
    except Exception as e:
        print(f"Request failed: {e}")

if __name__ == "__main__":
    asyncio.run(test_analyze())
