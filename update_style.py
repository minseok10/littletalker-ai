#!/usr/bin/env python3
"""
말투(STYLE.md) / 예시(examples.txt) 주기적 갱신 스크립트.

핵심: 전체 대화 흐름에서 이 사람이 언제·얼마나·어떻게 말하는지를 학습한다.
봇이 보낸 메시지를 학습에 포함할지는 실행 옵션으로 선택할 수 있다.

자주 돌릴 필요는 없다. 가끔(주 1회 등) 돌려서 톡방별 말투를 최신화하면 된다.

사용:
    python3 update_style.py --target "내톡방"
    python3 update_style.py --target "내톡방" --my-messages 200 --pairs 30
    # cron/launchd로 주기 실행도 가능

설정 우선순위 / .env 로딩은 kakao_bot 과 동일(헬퍼 재사용).
"""
import os
import re
import sys
import json
import argparse
import statistics
from collections import Counter
from datetime import datetime, timezone

import kakao_bot as kb  # parse_env_file/.env 로드, kakaocli, resolve_chat, fetch_messages 재사용


# 이름·별명·프로필 분석은 항상 Opus 4.8로 수행한다.
PROFILE_ANALYSIS_MODEL = "anthropic/claude-opus-4.8"


def infer_name(msgs):
    """is_from_me 메시지의 sender 최빈값 = 내 카톡 표시 이름. 없으면 ''."""
    names = Counter(m.get("sender") for m in msgs
                    if m.get("is_from_me") and m.get("sender"))
    return names.most_common(1)[0][0] if names else ""


def sanitize_names(raw, max_names=12, max_len=40):
    """LLM이 뽑은 이름/별명 정리 — 개행·제어문자·구분자(,) 분리·제거, 길이·개수 제한, 중복 제거.
    (대화 내용을 거친 LLM 출력이므로 config.env 키 주입·비정상 값 방지)"""
    seen, out = set(), []
    for r in raw or []:
        for piece in str(r).replace(",", "\n").split("\n"):
            n = "".join(ch for ch in piece if ord(ch) >= 0x20 and ch != "\x7f")
            n = " ".join(n.split()).strip()
            if n and "=" not in n and len(n) <= max_len and n not in seen:
                seen.add(n)
                out.append(n)
                if len(out) >= max_names:
                    return out
    return out


def infer_name_for_target(target, fetch_limit=400):
    """톡방 이름/ID로 메시지를 조금 읽어 내 표시이름을 추론한다(메뉴 미리보기용)."""
    chat_id, _ = kb.resolve_chat(target)
    return infer_name(kb.fetch_messages(chat_id, fetch_limit))


IDENTITY_SCHEMA = {
    "type": "object",
    "properties": {
        "names": {"type": "array", "items": {"type": "string"}},
        "primary_name": {"type": "string"},
        "profile": {"type": "string"},
    },
    "required": ["names", "primary_name", "profile"],
    "additionalProperties": False,
}


def build_transcript(all_msgs, max_msgs=300, max_len=120):
    """이름/별명·프로필 추출용 다자 대화 발췌(라벨 포함). 내 메시지는 '(이 사람)' 표시."""
    rows = [m for m in all_msgs if m.get("text")][-max_msgs:]
    lines = []
    for m in rows:
        who = m.get("sender") or "?"
        tag = f"{who} (이 사람)" if m.get("is_from_me") else who
        t = " ".join(m["text"].split())
        if len(t) > max_len:
            t = t[:max_len] + "…"
        lines.append(f"[{tag}] {t}")
    return "\n".join(lines)


def extract_identity(client, all_msgs, display_name):
    """Opus 4.8로 이름·별명·프로필을 추출한다(톡방별로 다름)."""
    transcript = build_transcript(all_msgs)
    prompt = f"""너는 카톡 단톡방 대화 로그를 분석한다.
분석 대상은 표시이름이 "{display_name or '(알 수 없음)'}"인 사람이고, 로그에서 이름 뒤에 "(이 사람)"으로 표시된다.
아래 두 가지를 뽑아라.

1) names: 이 사람이 '이 톡방에서' 불리는 모든 이름·별명(호칭). 남들이 부를 때 쓰는 반말 별명·줄임말,
   본인이 자기를 지칭하는 표현, 표시이름과 그 변형을 포함한다. 이 사람을 가리키는 게 확실한 것만 넣어라
   (다른 사람 호칭과 혼동 금지). 흔한 단어라 오탐이 날 만한 별명은 넣지 마라.
2) profile: 이 사람이 이 톡방에서 대화하는 데 도움이 될 프로필 — 역할, 주요 관계(누구와 친한지 등),
   자주 나오는 화제, 대화 태도 등을 관찰 가능한 사실 위주로 한국어 3~8줄.
primary_name 은 대표로 쓸 이름(보통 표시이름).

[대화 로그]
{transcript}
"""
    resp = client.messages.create(
        model=PROFILE_ANALYSIS_MODEL,
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}],
        output_config={"format": {"type": "json_schema", "schema": IDENTITY_SCHEMA}},
    )
    text = next((b.text for b in resp.content if b.type == "text"), "")
    return json.loads(text)


def gather_training_messages(chat_id, sent_ids, fetch_limit, include_bot_messages=False):
    """원본·학습용 타임라인과 학습 대상자의 텍스트 메시지를 반환한다."""
    msgs = kb.fetch_messages(chat_id, fetch_limit)
    sent = set(sent_ids)
    training_msgs = [
        m for m in msgs
        if include_bot_messages or not (m.get("is_from_me") and m["id"] in sent)
    ]
    mine = [
        m for m in training_msgs
        if m.get("is_from_me")
        and m.get("type") in ("text", "unknown")
        and m.get("text")
    ]
    return msgs, training_msgs, mine


def compute_stats(texts):
    n = len(texts)
    if n == 0:
        return {}
    lens = [len(t) for t in texts]
    runs = [len(m.group()) for t in texts for m in re.finditer(r"ㅋ+", t)]
    all_text = " ".join(texts)
    words = Counter(w for w in re.findall(r"[가-힣]+", all_text) if len(w) >= 2)
    endings = Counter(t.strip()[-2:] for t in texts if len(t.strip()) >= 2)
    def cnt(pred):
        return sum(1 for t in texts if pred(t))
    return {
        "count": n,
        "avg_len": round(statistics.mean(lens), 1),
        "median_len": statistics.median(lens),
        "max_len": max(lens),
        "kk_msgs": cnt(lambda t: "ㅋ" in t),
        "hh_msgs": cnt(lambda t: "ㅎ" in t),
        "emoji_msgs": cnt(lambda t: any(ord(c) > 0x1F000 for c in t)),
        "q_msgs": cnt(lambda t: "?" in t),
        "excl_msgs": cnt(lambda t: "!" in t),
        "tilde_msgs": cnt(lambda t: "~" in t),
        "nospace_msgs": cnt(lambda t: " " not in t.strip()),
        "top_words": words.most_common(20),
        "top_endings": endings.most_common(15),
    }


def compute_participation_stats(messages, mine_ids):
    """전체 대화에서 대상자의 발화 비율과 연속 전송(버스트) 정도를 계산한다."""
    mine_set = set(mine_ids)
    rows = [m for m in messages if m.get("text")]
    burst_lengths, current = [], 0
    for m in rows:
        if m["id"] in mine_set:
            current += 1
        elif current:
            burst_lengths.append(current)
            current = 0
    if current:
        burst_lengths.append(current)
    mine_count = sum(1 for m in rows if m["id"] in mine_set)
    return {
        "timeline_messages": len(rows),
        "target_messages": mine_count,
        "target_message_ratio": round(mine_count / len(rows), 3) if rows else 0,
        "target_bursts": len(burst_lengths),
        "avg_messages_per_burst": (round(statistics.mean(burst_lengths), 2)
                                   if burst_lengths else 0),
        "max_messages_per_burst": max(burst_lengths, default=0),
    }


def build_pairs(all_msgs, mine_ids, max_pairs, window_sec=300):
    """직전 남의 메시지 → 학습 대상자의 답장 쌍."""
    def ts(m):
        return datetime.fromisoformat(m["timestamp"].replace("Z", "+00:00"))
    mine_set = set(mine_ids)
    pairs = []
    for i, m in enumerate(all_msgs):
        if m["id"] not in mine_set or not m.get("text"):
            continue
        j = i - 1
        while j >= 0:
            p = all_msgs[j]
            if p.get("is_from_me"):
                j -= 1
                continue
            if p.get("text"):
                break
            j -= 1
        if j < 0:
            continue
        p = all_msgs[j]
        if (ts(m) - ts(p)).total_seconds() <= window_sec:
            pairs.append((p.get("sender", "상대"), p["text"], m["text"]))
    # 너무 많으면 균등 샘플
    if len(pairs) > max_pairs:
        step = len(pairs) / max_pairs
        pairs = [pairs[int(i * step)] for i in range(max_pairs)]
    return pairs


STYLE_SCHEMA_HINT = """
STYLE.md 는 아래 섹션을 갖춘 마크다운으로 작성하라(관찰 가능한 구체적 특징 위주, 추상적 형용사 금지):
0. 한 줄 요약
1. 발화 판단 — 어떤 상황에 끼어들고, 어떤 상황에는 침묵하는지
2. 발화 빈도와 주도성 — 먼저 말 걸기, 질문 반응, 잡담 개입, 화제 시작·전환 경향
3. 상황별 반응 — 이름 언급, 직접 질문, 단체 질문, 가벼운 잡담, 장난, 진지한 대화별 차이
4. 대화 리듬과 티키타카 — 받아치기 지속 정도, 한 번 끼면 몇 턴 이어가는지, 빠지는 시점
5. 메시지 길이와 끊어 보내기 — 평균/중앙값/분포, 연속 전송(버스트) 개수와 패턴
6. ㅋ/ㅎ/이모지·문장부호·띄어쓰기 습관
7. 자주 쓰는 종결 어미·단어·표현
8. 봇이 지켜야 할 행동·출력 규칙 — 응답 여부부터 메시지 개수·길이·말투까지 번호 목록
""".strip()


def generate_style(client, model, stats, participation_stats, sample_texts, transcript):
    sample = "\n".join(f"- {t}" for t in sample_texts)
    stats_json = json.dumps(stats, ensure_ascii=False, indent=2)
    participation_json = json.dumps(participation_stats, ensure_ascii=False, indent=2)
    prompt = f"""다음은 한 사람의 카톡 단톡방 메시지 통계와 전체 대화 흐름이다.
로그에서 분석 대상은 이름 뒤에 "(이 사람)"으로 표시된다.
문체만 요약하지 말고, 이 사람이 언제 말하고 언제 침묵하는지, 얼마나 자주 먼저 말을 거는지,
상황별 개입 방식, 티키타카 지속 정도, 메시지 길이와 끊어 보내기까지 행동 패턴 전체를 분석해
자동응답 봇이 그대로 재현할 수 있는 STYLE.md를 작성하라.

고정된 적극성 단계나 일반적인 카톡 화자상을 가정하지 말고 오직 제공된 관찰 자료에 근거하라.
응답 여부를 판단할 수 있도록 상황별로 "말하는 경우"와 "침묵하는 경우"를 구체적으로 구분하라.

{STYLE_SCHEMA_HINT}

[대상자 메시지 문체 통계]
{stats_json}

[전체 대화 참여 통계]
{participation_json}

[대상자 메시지 표본]
{sample}

[최근 전체 대화 흐름]
{transcript}

STYLE.md 본문만 출력하라(코드펜스 없이)."""
    resp = client.messages.create(
        model=model,
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )
    return next((b.text for b in resp.content if b.type == "text"), "").strip()


def main():
    p = argparse.ArgumentParser(description="톡방별 말투(STYLE.md) 갱신")
    p.add_argument("--target", help="톡방 이름(부분일치) 또는 chat-id")
    p.add_argument("--my-messages", dest="my_messages", type=int, default=180,
                   help="분석에 쓸 내 메시지 최대 개수")
    p.add_argument("--pairs", type=int, default=25, help="examples.txt 대화 쌍 수")
    p.add_argument("--fetch-limit", dest="fetch_limit", type=int, default=6000,
                   help="역추적해 읽을 전체 메시지 수")
    p.add_argument("--model", default=os.environ.get("STYLE_MODEL", kb.DEFAULTS["STYLE_MODEL"]))
    p.add_argument("--name", default=None,
                   help="봇이 흉내낼 사람 이름. 생략하면 메시지에서 자동 추론")
    bot_group = p.add_mutually_exclusive_group()
    bot_group.add_argument("--include-bot-messages", dest="include_bot_messages",
                           action="store_true", help="기존 봇 발화도 말투·행동 학습에 포함")
    bot_group.add_argument("--exclude-bot-messages", dest="include_bot_messages",
                           action="store_false", help="기존 봇 발화를 학습에서 제외(기본)")
    p.set_defaults(include_bot_messages=False)
    args = p.parse_args()

    if not os.environ.get("OPENROUTER_API_KEY"):
        print("ERROR: OPENROUTER_API_KEY 가 필요합니다.", file=sys.stderr)
        sys.exit(1)

    target = args.target or os.environ.get("TARGET", kb.DEFAULTS["TARGET"])
    room_dir = os.path.join(kb.ROOMS_DIR, kb.sanitize_room(target))
    os.makedirs(room_dir, exist_ok=True)
    state_path = os.path.join(room_dir, "state.json")
    style_path = os.path.join(room_dir, "STYLE.md")
    examples_path = os.path.join(room_dir, "examples.txt")

    sent_ids = []
    if os.path.exists(state_path):
        with open(state_path, encoding="utf-8") as f:
            sent_ids = json.load(f).get("sent_ids", [])

    chat_id, _ = kb.resolve_chat(target)
    _all_msgs, training_msgs, mine = gather_training_messages(
        chat_id, sent_ids, args.fetch_limit, args.include_bot_messages)
    if not mine:
        print(f"학습 대상자의 메시지를 찾지 못함 (target={target}).", file=sys.stderr)
        sys.exit(1)

    all_mine_ids = [m["id"] for m in mine]
    sample_mine = mine[-args.my_messages:]
    sample_mine_ids = [m["id"] for m in sample_mine]
    texts = [m["text"] for m in sample_mine]
    mode = "포함" if args.include_bot_messages else "제외"
    print(f"분석 대상 메시지 {len(texts)}개 · 봇 발화 {mode} "
          f"(기록된 봇 발화 {len(sent_ids)}개)")

    stats = compute_stats(texts)
    participation_stats = compute_participation_stats(training_msgs, all_mine_ids)
    behavior_transcript = build_transcript(training_msgs)
    client = kb.make_anthropic_client()

    # ── API 호출을 먼저 모두 끝낸다 ──────────────────────────────────
    # (이름 저장 후 STYLE 생성이 실패하면 '새 이름 + 옛 STYLE'로 정보가 섞이므로,
    #  모든 LLM 결과를 확보한 뒤에만 파일을 기록한다.)
    # 이름·별명·프로필을 Opus 4.8로 추출한다. 톡방마다 다르므로 톡방별로 저장한다.
    display_name = infer_name(training_msgs)  # sender 기반 표시이름(힌트·폴백)
    print(f"이름·별명·프로필 분석 중 ({PROFILE_ANALYSIS_MODEL})...")
    try:
        identity = extract_identity(client, training_msgs, display_name)
    except Exception as e:
        print(f"이름·프로필 LLM 추출 실패({e}) — 표시이름만 사용.", file=sys.stderr)
        identity = {"names": [display_name] if display_name else [],
                    "primary_name": display_name, "profile": ""}
    names = sanitize_names(identity.get("names"))
    primary_raw = args.name or identity.get("primary_name") or display_name or ""
    primary = (sanitize_names([primary_raw], max_names=1) or [""])[0]
    if primary and primary not in names:
        names.insert(0, primary)
    names = names[:12]
    profile = (identity.get("profile") or "").strip()[:2000]
    print("STYLE.md 생성 중...")
    try:
        style_body = generate_style(client, args.model, stats, participation_stats,
                                    texts[-120:], behavior_transcript)
    except Exception as e:
        print(f"STYLE.md 생성 실패: {e} — 아무것도 변경하지 않았습니다.", file=sys.stderr)
        sys.exit(1)
    pairs = build_pairs(training_msgs, sample_mine_ids, args.pairs)  # API 호출 아님

    # ── 여기부터 파일 기록 (모든 API 결과 확보 후, 원자적 교체) ──────────
    profile_section = "## 프로필 · 호칭 (자동 추출)\n\n"
    if names:
        profile_section += f"- 이 톡방에서 불리는 이름/별명: {', '.join(names)}\n"
    if profile:
        profile_section += f"\n{profile}\n"
    style_md = profile_section + "\n---\n\n" + style_body
    kb.write_text_atomic(style_path, style_md + "\n")
    print(f"  → {style_path}")

    lines = [
        '# examples.txt — "직전 메시지 → 내 답장" 대화 쌍',
        ("# (update_style.py 자동 생성. 봇 발화 "
         + ("포함" if args.include_bot_messages else "제외") + ")"),
        "",
    ]
    for sender, prev, reply in pairs:
        lines.append(f"상대> {prev}")
        lines.append(f"나> {reply}")
        lines.append("")
    kb.write_text_atomic(examples_path, "\n".join(lines))
    print(f"  → {examples_path} ({len(pairs)} 쌍)")

    # 이름·별명은 STYLE 저장이 끝난 뒤 마지막에 기록(불일치 창 최소화, 키 주입 방어)
    cfgpath = os.path.join(room_dir, "config.env")
    # 숫자 적극성 체계는 폐기되었으므로 이전 버전의 잔여 설정을 정리한다.
    kb.remove_config_keys(cfgpath, {"ASSERTIVENESS", "DETECTED_ASSERTIVENESS"})
    if primary:
        kb.update_config_value(cfgpath, "BOT_NAME", primary)
    if names:
        kb.update_config_value(cfgpath, "BOT_ALIASES", ", ".join(names))
    print("봇 이름: " + (primary or "(미설정)")
          + (f" · 이름/별명: {', '.join(names)}" if names else "")
          + (" (직접 지정)" if args.name else " (대화에서 자동 추출)"))
    print("완료. 다음 봇 실행부터 갱신된 말투가 적용됩니다.")


if __name__ == "__main__":
    main()
