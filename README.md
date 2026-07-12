# LittleTalker AI 🤖

> 나처럼 말하는 작은 AI

[수정판 kakaocli](https://github.com/minseok10/kakaocli/tree/local-build)로 카카오톡 단톡방을 폴링하면서,
톡방별로 학습한 **내 말투·이름·별명·프로필**(`STYLE.md`)로 **적절한 타이밍에만** 자율 응답하는 실험용 봇.
"언제 말하고 언제 침묵할지"와 "무슨 말투로 답할지"는 OpenRouter를 경유한 Claude가 판단하며,
설치 후 `littletalker` 명령으로 여는 숫자 입력식 메뉴에서 학습·실행·설정을 모두 다룰 수 있다.

언제 말하고 언제 침묵할지, 먼저 말을 거는 빈도, 상황별 반응, 티키타카 길이와 메시지 분할까지
고정 규칙 대신 톡방별 `STYLE.md`에 학습된 실제 행동 패턴을 따른다.

> ⚠️ **실험/연구용 프로젝트입니다.** 실제 사람들이 있는 단톡방에 *내 이름으로* 메시지가 나갑니다.
> 반드시 먼저 `DRY_RUN=true`(초안만 로그) 또는 `--use-self`(자기채팅으로만 전송)로 충분히 검증하세요.
> 자세한 주의사항은 맨 아래 [한계와 주의](#한계와-주의)를 읽어주세요.

---

## ✨ 특징

- **대화형 메뉴** — 설치 후 `littletalker` 명령 하나로 톡방 선택·학습·실행·설정을 숫자 입력으로 처리.
- **행동 패턴 전체 학습** — 발화 여부·빈도·주도성·상황별 반응·티키타카·길이·분할·문체를 `STYLE.md`에 통합 학습.
- **내 말투 모사** — 톡방별 `STYLE.md`(관찰된 행동·말투 규칙) + `examples.txt`(실제 대화 예시)를 시스템 프롬프트로 사용.
- **이름·별명·프로필 자동 학습** — 그 톡방 대화를 Opus로 분석해 내가 불리는 이름/별명(방마다 다름)과 관계·화제 프로필을 추출 → 응답 적합성 향상.
- **멀티 톡방** — 톡방마다 `rooms/<톡방>/` 폴더에 말투·프로필·상태·로그·설정을 따로 보관.
- **봇 발화 포함 여부 선택** — 기존 봇 발화를 학습에서 제외하거나, 개선된 봇 말투까지 포함해 재학습할지 톡방별로 선택.
- **설정 외부화** — 모델·맥락창·폴링·대기·발화 간격·학습 방식을 톡방별 `config.env`/CLI로 조정.
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

- **macOS 14 이상**과 데스크톱 **카카오톡**
- **OpenRouter API 키** — 종량제 키. https://openrouter.ai/keys 에서 발급
  (Anthropic SDK를 OpenRouter의 Anthropic 호환 엔드포인트로 라우팅해 Claude 모델을 호출한다)
- 사용하는 터미널에 macOS **전체 디스크 접근 권한**과 **손쉬운 사용(접근성)** 권한

간편 설치기는 Homebrew, Python 3.9+, SQLCipher, pkg-config를 확인하고 부족한 항목을 설치한다.
수정판 `kakaocli`를 소스에서 빌드하므로 Xcode Command Line Tools와 Swift도 필요하다.
Command Line Tools가 없으면 설치기가 macOS 설치 창을 열고, 설치 완료 후 같은 명령을 다시 실행하도록 안내한다.

---

## 🚀 빠른 시작

### 간편 설치 (권장)

아래 한 줄로 LittleTalker AI와 전용 수정판 `kakaocli`를 함께 설치한다.
수정판은 `minseok10/kakaocli`의 `local-build` 브랜치에서 직접 빌드되며,
시스템에 설치된 다른 `kakaocli`와 분리된다.

```bash
curl -fsSL https://raw.githubusercontent.com/minseok10/littletalker-ai/main/install.sh | sh
```

스크립트를 먼저 확인하고 실행하려면 다음처럼 내려받아 열어볼 수 있다.

```bash
curl -fsSLo /tmp/littletalker-install.sh \
  https://raw.githubusercontent.com/minseok10/littletalker-ai/main/install.sh
less /tmp/littletalker-install.sh
sh /tmp/littletalker-install.sh
```

설치가 끝난 뒤 새 터미널에서 실행한다.

```bash
littletalker
```

처음 설치할 때 OpenRouter API 키를 묻는다. Xcode Command Line Tools가 없는 경우에는
macOS 설치 창을 완료한 뒤 위 명령을 한 번 더 실행하면 된다. 같은 명령을 다시 실행하면
LittleTalker AI의 `main`과 수정판 `kakaocli`의 `local-build` 최신 내용으로 갱신된다.
기존 `.env`와 `rooms/` 데이터는 그대로 보존된다.

설치되는 위치는 다음과 같다.

```text
~/.local/share/littletalker-ai/
├── app/                 LittleTalker AI 코드, .env, rooms 데이터
├── src/kakaocli/        local-build 소스
├── bin/kakaocli         LittleTalker 전용 수정판 바이너리
└── venv/                전용 Python 가상환경
~/.local/bin/littletalker  대화형 메뉴 실행 명령
```

> 설치 폴더의 추적 대상 소스 파일을 직접 수정하면 다음 업데이트 때 원격 브랜치 내용으로 교체된다.
> 개발하려면 간편 설치 폴더가 아닌 별도의 Git clone을 사용하는 편이 안전하다.

#### 최초 권한 설정

1. 카카오톡 데스크톱 앱을 설치하고 로그인한다.
2. **시스템 설정 → 개인정보 보호 및 보안 → 전체 디스크 접근 권한**에서 사용하는 터미널을 허용한다.
3. 같은 화면의 **손쉬운 사용**에서도 해당 터미널을 허용한다.
4. 터미널을 완전히 종료했다가 다시 열고 `littletalker`를 실행한다.

전체 디스크 접근 권한은 메시지 DB를 읽는 데 필요하고, 손쉬운 사용 권한은 메시지를 전송할 때
카카오톡 UI를 조작하는 데 필요하다. 처음에는 메뉴의 기본값인 **나와의 채팅**으로 시험하는 것을 권장한다.

#### 업데이트와 삭제

업데이트는 설치 명령을 그대로 다시 실행하면 된다. API 키를 바꾸려면 다음 파일을 수정한다.

```bash
nano ~/.local/share/littletalker-ai/app/.env
```

삭제하기 전에 `rooms/`에 학습 결과와 개인 대화 기반 데이터가 있다는 점을 확인한다.
모든 데이터까지 완전히 삭제하려면 다음 명령을 사용한다.

```bash
rm -rf ~/.local/share/littletalker-ai
rm -f ~/.local/bin/littletalker
```

### 직접 설치

```bash
# 1) 빌드 의존성
brew install sqlcipher pkgconf

# 2) 수정판 kakaocli 빌드
git clone --branch local-build https://github.com/minseok10/kakaocli.git
cd kakaocli
swift build -c release --product kakaocli
mkdir -p ~/.local/bin
cp .build/release/kakaocli ~/.local/bin/kakaocli
cd ..

# 3) LittleTalker AI
git clone https://github.com/minseok10/littletalker-ai.git
cd littletalker-ai
python3 -m venv .venv
.venv/bin/pip install anthropic

# 4) API 키 — .env 에는 OPENROUTER_API_KEY만 입력
cp .env.example .env
nano .env

# 5) 대화형 메뉴
KAKAOCLI_BIN="$HOME/.local/bin/kakaocli" .venv/bin/python menu.py
```

> 처음엔 반드시 **dry-run**(초안만 로그) 또는 **`--use-self`**(나와의 채팅으로만 전송)로 충분히
> 검증하세요. 메뉴의 "봇 실행"은 기본적으로 나와의 채팅에 전송하며, 실제 톡방 전송은 `yes`로 한 번 더 확인합니다.
> 스크립트를 직접 실행하고 싶으면 아래 **사용** 섹션을 참고하세요.

---

## 📂 폴더 구조

```
menu.py               `littletalker` 명령이 실행하는 대화형 메뉴 본체
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
    config.env        이 톡방 전용 설정(학습 방식·이름/별명·모델 등) — git 제외
    STOP              있으면 이 톡방만 정지
```

`rooms/`와 `rooms/<톡방>/`은 처음 학습하거나 실행할 때 자동 생성된다.
**개인 대화 데이터(말투·프로필·예시·로그·상태·설정)는 `.gitignore`로 커밋에서 제외**된다.

---

## 🕹️ 사용

### 대화형 메뉴 — 권장

간편 설치했다면 `littletalker`를 실행한다. 숫자만 입력해 톡방을 고르고 봇을 다룰 수 있으며,
`kakao_bot.py`와 `update_style.py`의 인수를 외울 필요가 없다.

```bash
littletalker
```

소스를 직접 clone해 설치한 경우에만 다음처럼 메뉴 본체를 실행한다.

```bash
KAKAOCLI_BIN="$HOME/.local/bin/kakaocli" .venv/bin/python menu.py
```

```
  1) 봇 실행          톡방 목록에서 번호로 고름 → (말투 없으면 먼저 학습) →
                     전송 대상 선택 → 한 사이클/루프 선택
  2) 말투 학습        톡방 선택 → 봇 발화 포함 여부 → 행동·말투·이름·프로필 학습 → STYLE.md 갱신
  3) 톡방 목록 보기    카톡 톡방 + 학습 상태
  4) 톡방별 설정       봇 이름/별명 · 학습 파라미터 · 봇 발화 포함 여부 확인/변경
  0) 종료
```

- **말투 미학습 방**은 봇 실행 전에 학습을 먼저 유도하고, 학습이 실패하면 봇 실행을 취소한다.
- **이름·별명·프로필**은 말투 학습 과정에서 그 톡방 대화를 Opus로 분석해 자동 추출한다. 단톡방마다
  부르는 호칭(이름·별명)이 다르므로 톡방별로 저장된다 — 대표 이름은 `BOT_NAME`, 이 방에서 쓰이는
  이름/별명 목록은 `BOT_ALIASES`(봇이 "내가 불렸는지" 판단에 사용), 관계·화제 등 프로필은 `STYLE.md`의
  "프로필·호칭" 섹션에 담긴다. 학습 시 대표 이름을 직접 지정할 수 있고, 비우면 특정인 없이 "이 계정 주인"
  이라는 일반 문구로 동작한다(배포 기본값 — 커밋되는 코드/프롬프트에 개인정보 없음).
- **행동·말투 학습**은 전체 대화 흐름에서 언제 말하고 침묵하는지, 발화 빈도와 선제 발화,
  상황별 반응, 티키타카 지속 정도, 메시지 길이·분할과 문체를 함께 분석해 `STYLE.md`에 기록한다.
  별도의 숫자 적극성 설정은 없으며 자동응답 판단은 이 학습 결과를 직접 따른다.
- **봇 발화 학습 여부**는 학습할 때 메뉴에서 포함/제외를 선택하고, 선택은 톡방별
  `INCLUDE_BOT_MESSAGES` 설정으로 기억된다. 기본값은 제외다.
- 전송 대상은 **나와의 채팅(기본)**·실제 톡방·dry-run 중에서 고른다. 실제 톡방 전송은 `yes`로
  한 번 더 확인하며, 확인하지 않으면 나와의 채팅으로 전환된다.
- **학습 파라미터**(`--my-messages`·`--pairs`·`--fetch-limit`·`--model`)는 학습 시 조정할 수 있고,
  `rooms/<톡방>/config.env`(`MY_MESSAGES`·`PAIRS`·`STYLE_FETCH_LIMIT`·`STYLE_MODEL`)에 저장돼 톡방별로 유지된다.

### 소스에서 직접 실행 (`kakao_bot.py`)

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
| `USE_SELF` / `--use-self`,`--no-use-self` | false | 읽기는 TARGET, 전송은 '나와의 채팅' 또는 TARGET으로 |
| `MODEL` / `--model` | anthropic/claude-sonnet-5 | 봇 응답 판단 모델 (OpenRouter 슬러그) |
| `STYLE_MODEL` / `update_style.py --model` | anthropic/claude-opus-4.8 | 말투 갱신 모델 (OpenRouter 슬러그) |
| `CONTEXT_LIMIT` / `--context-limit` | 40 | LLM에 넘기는 최근 맥락 메시지 수 |
| `POLL_SECONDS` / `--poll-seconds` | 10 | 루프 폴링 주기(초) |
| `DELAY_MIN` / `--delay-min` | 3 | 응답 전 랜덤 대기 하한(초) |
| `DELAY_MAX` / `--delay-max` | 9 | 상한 |
| `MIN_GAP` / `--min-gap` | 0 | 선택적 안전 간격. 0이면 STYLE.md의 대화 리듬을 제한하지 않음 |
| `MAX_PER_HOUR` / `--max-per-hour` | 0 | 시간당 최대 발화(0=무제한) |
| `FETCH_LIMIT` / `--fetch-limit` | 80 | 한 번에 읽는 메시지 수 |
| `BOT_NAME` / `--name` | (학습 시 자동 추출) | 봇이 흉내낼 사람 대표 이름. 비우면 "이 계정 주인"으로 동작 |
| `BOT_ALIASES` | (학습 시 자동 추출) | 이 톡방에서 나를 부르는 이름/별명(쉼표 구분). "내가 불렸는지" 판단에 사용 |
| `INCLUDE_BOT_MESSAGES` / `--include-bot-messages`,`--exclude-bot-messages` | false | 기존 봇 발화를 행동·말투 학습에 포함할지 여부 |

톡방마다 다른 값을 주고 싶으면 `rooms/<톡방>/config.env`에 같은 키를 쓰면 된다.

---

## 🗣️ 말투 갱신 (`update_style.py`)

그 톡방에서 대화하는 데 필요한 걸 학습해 `STYLE.md`·`examples.txt`를 다시 생성한다:

- **행동·말투** — 전체 대화 흐름과 대상자의 메시지 통계를 함께 분석해 발화 판단·빈도·주도성·상황별
  반응·티키타카·길이·분할·문체를 하나의 `STYLE.md`로 만든다.
- **봇 발화 선택** — 기본은 `state.json`의 `sent_ids`를 이용해 기존 봇 발화를 제외한다. 메뉴 또는
  `--include-bot-messages`로 포함할 수 있으며, 직접 실행에서는 `--exclude-bot-messages`로 명시적으로 제외할 수 있다.
- **이름·별명·프로필** — Opus가 그 톡방 대화를 분석해 내가 불리는 이름/별명(단톡방마다 다름)과
  관계·화제 등 프로필을 추출한다. 이름/별명은 `config.env`(`BOT_NAME`·`BOT_ALIASES`)에, 프로필은
  `STYLE.md`의 "프로필·호칭" 섹션에 저장돼 응답 적합성을 높인다.

자주 할 필요는 없고 가끔(예: 주 1회) 돌리면 된다.

```bash
.venv/bin/python update_style.py --target "톡방이름"
.venv/bin/python update_style.py --target "톡방이름" --my-messages 200 --pairs 30
.venv/bin/python update_style.py --target "톡방이름" --include-bot-messages
.venv/bin/python update_style.py --target "톡방이름" --name "홍길동"   # 대표 이름 직접 지정(별명은 여전히 자동 추출)
```

처음 쓰는 톡방은 이 스크립트로 `STYLE.md`를 먼저 만들어 두면 된다.

---

## 🛡️ 안전장치

- **루프 방지(최우선)** — 내/봇 메시지엔 반응하지 않음(`is_from_me`). `sent_ids`에는 **봇이 방금 보낸 메시지만** 최대 10,000개 기록해, 내가 직접 친 메시지가 봇 발화로 오분류되지 않게 하고 재학습의 포함/제외 선택에 사용
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
| `littletalker: command not found` | 설치 후 새 터미널을 열거나 `source ~/.zprofile` 실행. 그래도 안 되면 `~/.local/bin/littletalker`가 있는지 확인 |
| 설치 중 `kakaocli 빌드에 실패했습니다` | macOS 소프트웨어 업데이트에서 Xcode Command Line Tools를 갱신. `swift --version`과 `xcode-select -p`도 확인. 전체 Xcode를 쓴다면 올바른 Xcode가 선택됐는지 확인 |
| `OPENROUTER_API_KEY 가 필요합니다` | `.env`에 키를 넣었는지 확인. VS Code라면 환경변수 주입 설정 영향일 수 있음 — 셸에서 직접 실행해보기 |
| `401 authentication_error` / `Invalid bearer token` (키는 유효한데 실패) | 환경에 `ANTHROPIC_BASE_URL`이 설정돼 있으면 SDK가 OpenRouter가 아닌 그쪽으로 요청을 보냄. `unset ANTHROPIC_BASE_URL` 후 `littletalker`를 다시 실행 |
| 톡방 목록을 읽지 못함 | 터미널의 전체 디스크 접근 권한을 확인하고 터미널을 완전히 재시작. 설치판 진단은 `~/.local/share/littletalker-ai/bin/kakaocli chats`로 가능 |
| 전송이 안 되고 `not found in the chat list` | kakaocli가 접근성 트리에서 톡방 이름을 못 찾는 경우. 카카오톡 버전에 따라 이름이 담긴 AX 노드 식별자가 바뀔 수 있음. 설치판의 전용 바이너리인지 확인하고 이슈에 카카오톡 버전과 증상을 첨부 |
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
