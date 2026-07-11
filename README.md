# 카톡 단톡방 자율 응답 봇 🤖

[kakaocli](https://github.com/silver-flight-group/kakaocli)로 카카오톡 단톡방을 폴링하면서,
톡방별로 학습한 **내 말투·이름·별명·프로필**(`STYLE.md`)로 **적절한 타이밍에만** 자율 응답하는 실험용 봇.
"언제 말하고 언제 침묵할지"와 "무슨 말투로 답할지"는 OpenRouter를 경유한 Claude가 판단하며,
숫자 입력식 대화형 메뉴(`menu.py`)로 학습·실행·설정을 모두 다룰 수 있다.

핵심 설계 철학은 **"대부분의 순간엔 침묵이 정답"** 이다. 봇은 도배하지 않고,
이름이 불리거나·질문이 오거나·끼어드는 게 자연스러운 순간에만 짧게 반응한다.

> ⚠️ **실험/연구용 프로젝트입니다.** 실제 사람들이 있는 단톡방에 *내 이름으로* 메시지가 나갑니다.
> 반드시 먼저 `DRY_RUN=true`(초안만 로그) 또는 `--use-self`(자기채팅으로만 전송)로 충분히 검증하세요.
> 자세한 주의사항은 맨 아래 [한계와 주의](#한계와-주의)를 읽어주세요.

---

## ✨ 특징

- **대화형 메뉴** — `menu.py` 하나로 톡방 선택·학습·실행·설정을 숫자 입력으로 처리(권장 진입점).
- **침묵이 기본** — 무조건 답하지 않는다. 끼어드는 게 자연스러운 순간만 고른다.
- **응답 적극성 조절(1~5)** — 거의 침묵부터 매우 적극적까지 톡방별로 조절하고, 레벨별 반응을 같은 예시 대화로 미리 본다.
- **내 말투 모사** — 톡방별 `STYLE.md`(관찰된 말투 규칙) + `examples.txt`(실제 대화 예시)를 시스템 프롬프트로 사용.
- **이름·별명·프로필 자동 학습** — 그 톡방 대화를 Opus로 분석해 내가 불리는 이름/별명(방마다 다름)과 관계·화제 프로필을 추출 → 응답 적합성 향상.
- **멀티 톡방** — 톡방마다 `rooms/<톡방>/` 폴더에 말투·프로필·상태·로그·설정을 따로 보관.
- **학습은 내 메시지만** — 봇이 보낸 메시지는 빼고 *내가 직접 친 메시지만*으로 `STYLE.md`를 재생성(자기 말투 오염 방지).
- **티키타카 지원** — 대화가 이어지는 중엔 짧은 메시지 여러 개로 자연스럽게 주고받는다.
- **설정 외부화** — 모델·맥락창·폴링·대기·발화 간격·적극성 등을 톡방별 `config.env`/CLI로 조정.
- **안전장치 다수** — 루프 방지, 중복 응답 방지, 발화 간격·시간당 상한, 랜덤 대기, `DRY_RUN`·`--use-self`, `STOP` 킬 스위치, 설정 주입 방어.

---

## 🔁 동작 원리

```
        ┌──────────────────────────────────────────────────────────┐
        │                    매 POLL_SECONDS 초                       │
        └──────────────────────────────────────────────────────────┘
                                   │
   kakaocli messages --json        ▼
   (카톡 DB 직접 읽기, 앱 조작 X)  ┌────────────┐
   ─────────────────────────────▶ │  새 메시지   │
                                   │   필터링    │  ← 내/봇 메시지 제외(루프 방지),
                                   └────────────┘     이미 응답한 것 제외
                                          │
                       침묵해야 할 상황?   ▼  (간격·상한·직전 내 발화 체크)
                                   ┌────────────┐
                                   │   Claude    │  ← STYLE.md + examples.txt 를
                                   │  응답 판단   │     시스템 프롬프트로,
                                   │  + 초안 작성 │     구조화 출력(JSON)으로 결정
                                   └────────────┘
                              should_respond? │
                          ┌──── false ────────┼──── true ────┐
                          ▼                                   ▼
                    [SILENT] 로그만               랜덤 대기 후 kakaocli send
                                                  (DRY_RUN이면 [SKIP])
                                                          │
                                                          ▼
                                            전송분을 sent_ids에 기록
                                            → 다음 사이클에 자기 말엔 반응 안 함
```

읽기(`messages`)는 카카오톡 로컬 DB를 직접 읽고, 전송(`send`)만 macOS 접근성으로 UI를 자동화한다.
따라서 전송하려면 카카오톡 앱이 로그인된 채 떠 있어야 한다.

---

## 📋 요구사항

- **macOS** + [kakaocli](https://github.com/silver-flight-group/kakaocli)
  (시스템 설정에서 **전체 디스크 접근 권한**과 **손쉬운 사용(접근성)** 권한 필요)
- **Python 3.9+** — 가상환경(`python3 -m venv .venv`) 권장, 의존성은 `anthropic` 하나
  (Homebrew 파이썬 등은 PEP 668로 `pip` 직접 설치가 막혀 있어 venv가 사실상 필수)
- **OpenRouter API 키** — 종량제 키. https://openrouter.ai/keys 에서 발급
  (Anthropic SDK를 OpenRouter의 Anthropic 호환 엔드포인트로 라우팅해 Claude 모델을 호출한다)
- 데스크톱 카카오톡에 로그인되어 있어야 함 (전송 시 메인 화면 상태)

---

## 🚀 빠른 시작

```bash
# 1) 가상환경 + 의존성 (PEP 668 때문에 venv 권장)
python3 -m venv .venv
.venv/bin/pip install anthropic

# 2) API 키 — .env 에는 OPENROUTER_API_KEY 만 채우면 됩니다 (다른 설정은 불필요)
cp .env.example .env

# 3) 대화형 메뉴로 톡방 선택 → 학습 → 실행까지 한 곳에서
.venv/bin/python menu.py
#   전송은 기본이 dry-run(초안만)이라 처음부터 안전합니다.
#   메뉴에서: 2) 말투 학습 으로 STYLE.md/이름·별명·프로필 생성 → 1) 봇 실행
```

> 처음엔 반드시 **dry-run**(초안만 로그) 또는 **`--use-self`**(나와의 채팅으로만 전송)로 충분히
> 검증하세요. 메뉴의 "봇 실행"은 실제 전송 여부를 매번 물어보고, 확인을 통과하지 못하면 dry-run으로 진행합니다.
> 스크립트를 직접 실행하고 싶으면 아래 **사용** 섹션을 참고하세요.

---

## 📂 폴더 구조

```
menu.py               대화형 메뉴(권장 진입점) — 아래 두 스크립트를 대신 실행
kakao_bot.py          봇 본체 (읽기·응답 판단·전송)
update_style.py       학습 스크립트 (말투 + 이름·별명·프로필)
.env                  OpenRouter API 키(시크릿) — git 제외
.env.example          .env 템플릿
rooms/
  <톡방>/             폴더 이름은 chat-id(기본) 또는 톡방 이름
    STYLE.md          "프로필·호칭" 섹션 + 말투 규칙
    examples.txt      대화 예시 (직전 메시지 → 내 답장)
    state.json        처리 상태(중복 방지·봇 발화 이력) — git 제외
    bot_log.jsonl     로그(JSON Lines) — git 제외
    config.env        이 톡방 전용 설정(적극성·이름/별명·모델 등) — git 제외
    STOP              있으면 이 톡방만 정지
```

`rooms/<톡방>/` 폴더는 없으면 자동 생성된다. **개인 대화 데이터(말투·프로필·예시·로그·상태·설정)는 `.gitignore`로 커밋에서 제외**되며, 폴더 구조(`rooms/.gitkeep`)만 저장소에 올라간다.

---

## 🕹️ 사용

### 대화형 메뉴 (`menu.py`) — 권장

숫자만 입력해 톡방을 고르고 봇을 다룰 수 있는 대화형 진입점이다. 인수를 외울 필요 없이
`kakao_bot.py`/`update_style.py`를 대신 실행해 준다.

```bash
.venv/bin/python menu.py
```

```
  1) 봇 실행          톡방 목록에서 번호로 고름 → (말투 없으면 먼저 학습) →
                     응답 적극성 확인/변경 → 실제 전송 여부 확인 → 한 사이클/루프 선택
  2) 말투 학습        톡방 선택 → (대표 이름 지정/자동) → 대화에서 이름·별명·프로필·말투 학습 → STYLE.md 갱신
  3) 톡방 목록 보기    카톡 톡방 + 학습·적극성 상태
  4) 톡방별 설정       응답 적극성 · 봇 이름/별명 · 학습 파라미터 등 config.env 확인/변경
  0) 종료
```

- **말투 미학습 방**은 봇 실행 전에 학습을 먼저 유도하고, 학습이 실패하면 봇 실행을 취소한다.
- **이름·별명·프로필**은 말투 학습 과정에서 그 톡방 대화를 Opus로 분석해 자동 추출한다. 단톡방마다
  부르는 호칭(이름·별명)이 다르므로 톡방별로 저장된다 — 대표 이름은 `BOT_NAME`, 이 방에서 쓰이는
  이름/별명 목록은 `BOT_ALIASES`(봇이 "내가 불렸는지" 판단에 사용), 관계·화제 등 프로필은 `STYLE.md`의
  "프로필·호칭" 섹션에 담긴다. 학습 시 대표 이름을 직접 지정할 수 있고, 비우면 특정인 없이 "이 계정 주인"
  이라는 일반 문구로 동작한다(배포 기본값 — 커밋되는 코드/프롬프트에 개인정보 없음).
- **응답 적극성(1~5)** 은 얼마나 적극적으로 끼어들지를 조절하며, 고를 때 레벨별 반응을
  같은 예시 대화로 보여준다. 값은 `rooms/<톡방>/config.env`의 `ASSERTIVENESS`에 저장된다.
- 실제 전송은 실행 직전 매번 확인하며, 확인을 통과하지 못하면 자동으로 dry-run으로 진행한다.
- **학습 파라미터**(`--my-messages`·`--pairs`·`--fetch-limit`·`--model`)는 학습 시 조정할 수 있고,
  `rooms/<톡방>/config.env`(`MY_MESSAGES`·`PAIRS`·`STYLE_FETCH_LIMIT`·`STYLE_MODEL`)에 저장돼 톡방별로 유지된다.

### 직접 실행 (`kakao_bot.py`)

```bash
# 한 사이클(테스트)
.venv/bin/python kakao_bot.py --target "톡방이름"

# 지속 가동(폴링 루프)
.venv/bin/python kakao_bot.py --target "톡방이름" --loop

# 실제 전송 + 설정 조정
.venv/bin/python kakao_bot.py --target "톡방이름" --no-dry-run --loop \
    --context-limit 50 --poll-seconds 8 --delay-min 3 --delay-max 9

# 안전 모드: 읽기는 단톡방, 전송은 '나와의 채팅'으로만 (실전 전 리허설용)
.venv/bin/python kakao_bot.py --target "톡방이름" --no-dry-run --use-self --loop
```

**정지:** `touch STOP`(전체) 또는 `touch "rooms/<톡방>/STOP"`(해당 톡방만). 재개하려면 파일 삭제.

---

## ⚙️ 설정

우선순위: **명령줄 인수 > 환경변수/`.env` > 톡방별 `config.env` > 내장 기본값**

> `.env` 에는 `OPENROUTER_API_KEY` 만 두는 것을 권장한다. 아래 설정들은 미리 정의해두지 않아도 되고,
> 넣지 않으면 표의 기본값이 쓰인다. 톡방별로 바꾸려면 `rooms/<톡방>/config.env`(대화형 메뉴에서 관리)에,
> 일회성으로는 CLI 인수로 주면 된다. (`.env` 에 전역으로 넣으면 `config.env`보다 우선하니 주의)

| 키 / 인수 | 기본값 | 설명 |
|---|---|---|
| `TARGET` / `--target` | 내톡방 | 톡방 이름(부분일치) 또는 chat-id |
| `DRY_RUN` / `--dry-run`,`--no-dry-run` | true | 초안만 vs 실제 전송 |
| `USE_SELF` / `--use-self` | false | 읽기는 TARGET, 전송은 '나와의 채팅'으로 |
| `MODEL` / `--model` | anthropic/claude-sonnet-5 | 봇 응답 판단 모델 (OpenRouter 슬러그) |
| `STYLE_MODEL` / `update_style.py --model` | anthropic/claude-opus-4.8 | 말투 갱신 모델 (OpenRouter 슬러그) |
| `CONTEXT_LIMIT` / `--context-limit` | 40 | LLM에 넘기는 최근 맥락 메시지 수 |
| `POLL_SECONDS` / `--poll-seconds` | 10 | 루프 폴링 주기(초) |
| `DELAY_MIN` / `--delay-min` | 3 | 응답 전 랜덤 대기 하한(초) |
| `DELAY_MAX` / `--delay-max` | 9 | 상한 |
| `MIN_GAP` / `--min-gap` | 5 | 내 마지막 발화 후 최소 간격(초) |
| `MAX_PER_HOUR` / `--max-per-hour` | 0 | 시간당 최대 발화(0=무제한) |
| `FETCH_LIMIT` / `--fetch-limit` | 80 | 한 번에 읽는 메시지 수 |
| `ASSERTIVENESS` / `--assertiveness` | 3 | 응답 적극성 1(거의 침묵)~5(매우 적극적) |
| `BOT_NAME` / `--name` | (학습 시 자동 추출) | 봇이 흉내낼 사람 대표 이름. 비우면 "이 계정 주인"으로 동작 |
| `BOT_ALIASES` | (학습 시 자동 추출) | 이 톡방에서 나를 부르는 이름/별명(쉼표 구분). "내가 불렸는지" 판단에 사용 |

톡방마다 다른 값을 주고 싶으면 `rooms/<톡방>/config.env`에 같은 키를 쓰면 된다.

---

## 🗣️ 말투 갱신 (`update_style.py`)

그 톡방에서 대화하는 데 필요한 걸 학습해 `STYLE.md`·`examples.txt`를 다시 생성한다:

- **말투** — 봇이 보낸 메시지는 빼고 **내가 직접 친 메시지만**으로 분석(봇 발화는 `state.json`의 `sent_ids`로 구분).
- **이름·별명·프로필** — Opus가 그 톡방 대화를 분석해 내가 불리는 이름/별명(단톡방마다 다름)과
  관계·화제 등 프로필을 추출한다. 이름/별명은 `config.env`(`BOT_NAME`·`BOT_ALIASES`)에, 프로필은
  `STYLE.md`의 "프로필·호칭" 섹션에 저장돼 응답 적합성을 높인다.

자주 할 필요는 없고 가끔(예: 주 1회) 돌리면 된다.

```bash
.venv/bin/python update_style.py --target "톡방이름"
.venv/bin/python update_style.py --target "톡방이름" --my-messages 200 --pairs 30
.venv/bin/python update_style.py --target "톡방이름" --name "홍길동"   # 대표 이름 직접 지정(별명은 여전히 자동 추출)
```

처음 쓰는 톡방은 이 스크립트로 `STYLE.md`를 먼저 만들어 두면 된다.

---

## 🛡️ 안전장치

- **루프 방지(최우선)** — 내/봇 메시지엔 반응하지 않음(`is_from_me`). `sent_ids`에는 **봇이 방금 보낸 메시지만** 기록해, 내가 직접 친 메시지가 봇 발화로 오분류되어 학습에서 빠지지 않게 함
- **중복 응답 방지** — 같은 메시지에 두 번 답하지 않음 (`responded_ids`)
- **발화 간격·시간당 상한** — 내 마지막 발화 후 최소 간격(`MIN_GAP`), 선택적 시간당 상한(`MAX_PER_HOUR`)
- **사람처럼** — 응답 전 랜덤 대기, 여러 메시지 사이 랜덤 간격
- **사후 검증** — 전송 전 `[DRAFT]`, 전송 후 `[SENT]` 로그(JSON Lines)
- **킬 스위치 / 안전 모드** — `STOP` 파일, `DRY_RUN`, `--use-self`
- **설정 주입 방어** — `config.env` 값은 키 형식 검증 + 개행/제어문자 제거로 기록(대화→LLM 추출값이 다른 설정 키를 주입하지 못하게)
- **원자적 학습** — 모든 LLM 결과를 받은 뒤에만 파일을 교체 기록. 학습이 중간에 실패하면 기존 `STYLE.md`/설정을 그대로 둠
- **자동 재시도** — API 일시 과부하(429/529)·네트워크 오류 시 자동 재시도(`max_retries=4`)

---

## 🔧 트러블슈팅

| 증상 | 원인 / 해결 |
|---|---|
| `OPENROUTER_API_KEY 가 필요합니다` | `.env`에 키를 넣었는지 확인. VS Code라면 환경변수 주입 설정 영향일 수 있음 — 셸에서 직접 실행해보기 |
| `401 authentication_error` / `Invalid bearer token` (키는 유효한데 실패) | 환경에 `ANTHROPIC_BASE_URL`이 설정돼 있으면 SDK가 OpenRouter가 아닌 그쪽으로 요청을 보냄. `unset ANTHROPIC_BASE_URL` 후 실행하거나, 그 변수가 없는 깨끗한 셸에서 `.venv/bin/python menu.py` 실행 |
| 전송이 안 되고 `not found in the chat list` | kakaocli가 접근성 트리에서 톡방 이름을 못 찾는 경우. 카카오톡 버전에 따라 이름이 담긴 AX 노드 식별자가 바뀜 → `kakaocli inspect`로 확인 후 kakaocli 소스(`findChatRow`) 점검 |
| 전송 시 `launching` 상태로 실패 | 카카오톡 앱이 메인(로그인) 화면이어야 함. 재로그인 후 재시도 |
| `OverloadedError: 529` / 5xx | 업스트림(OpenRouter·Claude) 일시 과부하. 코드 버그 아님 — 자동 재시도되며 루프 모드는 다음 사이클에 복구 |
| 읽기는 되는데 전송만 안 됨 | 전송은 UI 자동화라 **손쉬운 사용(접근성)** 권한 필요. 읽기는 **전체 디스크 접근** 권한 |

---

## ⚠️ 한계와 주의

- 이 프로젝트는 **개인 학습·실험 목적**입니다. 카카오톡의 약관/정책상 자동화는 제한될 수 있으니 본인 책임하에 사용하세요.
- 실제 단톡방에 **내 이름으로** 메시지가 나갑니다. 상대방을 속이거나 피해를 줄 수 있는 용도로 쓰지 마세요.
- LLM은 부적절하거나 사실과 다른 말을 생성할 수 있습니다. 실전 전송 전 `DRY_RUN`/`--use-self`로 충분히 검증하세요.
- `STYLE.md`·`examples.txt`·로그에는 **개인 대화 내용**이 담깁니다. 저장소에 커밋되지 않도록 `.gitignore`가 막고 있으니 임의로 추가하지 마세요.
- OpenRouter API는 **종량제 비용**이 발생합니다.

---

## 📄 라이선스

[MIT License](LICENSE). 자유롭게 참고·수정·재배포할 수 있으나, 사용에 따른 책임은 사용자에게 있습니다.
