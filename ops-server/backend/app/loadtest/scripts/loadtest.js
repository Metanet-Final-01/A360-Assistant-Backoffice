import http from 'k6/http';
import { check, sleep } from 'k6';
import { textSummary } from 'https://jslib.k6.io/k6-summary/0.0.2/index.js';

// 범용 부하테스트 스크립트 — 로컬에서 그대로 `k6 run`으로 돌린다(Ops가 대신 실행해주지
// 않음 — 직접 CLI로 돌리는 것보다 UI를 거치는 게 더 번거롭다는 판단). 대신 끝나면
// handleSummary()가 결과를 Ops로 자동 POST해서, 터미널에서 보던 요약을 그대로 보면서도
// Ops 쪽 이력·추세 차트에 자동으로 쌓이게 한다.
//
// 사용법:
//   k6 run loadtest.js -e TARGET_URL=http://127.0.0.1:8000/api/rag/search?q=엑셀 \
//     -e LABEL=rag-search -e PEAK_VUS=20 [-e METHOD=POST -e BODY='{"message":"..."}']
const TARGET_URL = __ENV.TARGET_URL;
const METHOD = (__ENV.METHOD || 'GET').toUpperCase();
const BODY = __ENV.BODY || null;
const PEAK_VUS = Math.max(1, parseInt(__ENV.PEAK_VUS || '20', 10));
const LABEL = __ENV.LABEL || 'loadtest';
const OPS_URL = (__ENV.OPS_URL || 'http://127.0.0.1:8100').replace(/\/$/, '');

export const options = {
  scenarios: {
    ramping: {
      executor: 'ramping-vus',
      startVUs: 0,
      stages: [
        { duration: '15s', target: Math.max(1, Math.round(PEAK_VUS * 0.2)) },
        { duration: '20s', target: Math.max(1, Math.round(PEAK_VUS * 0.2)) },
        { duration: '15s', target: Math.max(1, Math.round(PEAK_VUS * 0.6)) },
        { duration: '20s', target: Math.max(1, Math.round(PEAK_VUS * 0.6)) },
        { duration: '15s', target: PEAK_VUS },
        { duration: '20s', target: PEAK_VUS },
        { duration: '10s', target: 0 },
      ],
    },
  },
};

export default function () {
  const params = { timeout: '30s' };
  let res;
  if (METHOD === 'POST') {
    params.headers = { 'Content-Type': 'application/json' };
    res = http.post(TARGET_URL, BODY || '{}', params);
  } else {
    res = http.get(TARGET_URL, params);
  }
  check(res, { 'status is 2xx': (r) => r.status >= 200 && r.status < 300 });
  sleep(0.2);
}

export function handleSummary(data) {
  // Ops가 죽어 있거나 URL이 틀려도 부하테스트 자체·터미널 출력은 그대로 되게 try/catch로
  // 감싼다 — 업로드는 부가 기능이지 테스트의 필수 조건이 아니다.
  try {
    http.post(
      `${OPS_URL}/loadtest/upload`,
      JSON.stringify({
        summary: data, label: LABEL, target_url: TARGET_URL, peak_vus: PEAK_VUS, method: METHOD,
      }),
      { headers: { 'Content-Type': 'application/json' }, timeout: '10s' },
    );
  } catch (e) {
    console.error(`Ops 업로드 실패(무시하고 계속): ${e}`);
  }
  return { stdout: textSummary(data, { indent: ' ', enableColors: true }) };
}
