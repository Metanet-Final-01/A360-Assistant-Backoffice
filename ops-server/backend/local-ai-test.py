import json
import sys

import requests


SERVER_IP = "192.168.1.147"
SERVER_PORT = 8820

API_URL = f"http://{SERVER_IP}:{SERVER_PORT}/v1/chat/completions"

MODEL_NAME = "EXAONE-4.5-33B"

REQUEST_TIMEOUT_SECONDS = 180


def main() -> None:
    payload = {
        "model": MODEL_NAME,
        "messages": [
            {
                "role": "system",
                "content": (
                    "당신은 정확하고 간결하게 답변하는 한국어 AI 어시스턴트입니다."
                ),
            },
            {
                "role": "user",
                "content": "a360이 뭐야?",
            },
        ],
        "temperature": 0.6,
        "top_p": 0.95,
        "max_tokens": 2048,
        "stream": False,
    }

    headers = {
        "Content-Type": "application/json",
    }

    print(f"요청 주소: {API_URL}")
    print(f"모델: {MODEL_NAME}")
    print("LLM 요청 시작...")

    try:
        response = requests.post(
            API_URL,
            headers=headers,
            json=payload,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )

        print(f"HTTP 상태 코드: {response.status_code}")

        response.raise_for_status()

        result = response.json()

    except requests.exceptions.ConnectTimeout:
        print("연결 시간 초과: 서버 IP, 포트 또는 방화벽을 확인하세요.")
        sys.exit(1)

    except requests.exceptions.ReadTimeout:
        print(
            "응답 시간 초과: 모델 생성이 오래 걸리고 있습니다. "
            "timeout 값을 늘려보세요."
        )
        sys.exit(1)

    except requests.exceptions.ConnectionError as error:
        print(f"연결 오류: {error}")
        print()
        print("확인 사항:")
        print("1. llama-server가 실행 중인지")
        print("2. 서버가 0.0.0.0:8820에서 수신 중인지")
        print("3. 192.168.1.147에 접근 가능한 네트워크인지")
        print("4. 서버 방화벽에서 TCP 8820이 허용됐는지")
        sys.exit(1)

    except requests.exceptions.HTTPError:
        print("서버 오류 응답:")
        print(response.text)
        sys.exit(1)

    except json.JSONDecodeError:
        print("JSON이 아닌 응답을 받았습니다:")
        print(response.text)
        sys.exit(1)

    try:
        answer = result["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        print("예상하지 못한 응답 구조입니다:")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        sys.exit(1)

    print()
    print("[모델 응답]")
    print(answer)

    usage = result.get("usage")

    if usage:
        print()
        print()
        #print("[전체 JSON 응답]")
        #print(json.dumps(result, ensure_ascii=False, indent=2))
        print("[토큰 사용량]")
        print(f"prompt_tokens: {usage.get('prompt_tokens')}")
        print(f"completion_tokens: {usage.get('completion_tokens')}")
        print(f"total_tokens: {usage.get('total_tokens')}")


if __name__ == "__main__":
    main()