#!/usr/bin/env python3
"""
말투(STYLE.md) / 예시(examples.txt) 주기적 갱신 스크립트.

핵심: 내가 "직접 친" 메시지만으로 말투를 학습한다.
  - is_from_me=true 중에서, 봇이 보낸 메시지(rooms/<톡방>/state.json 의 sent_ids)는 제외.
  - 남은 게 진짜 내 말투 데이터.

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


def infer_name(msgs):
    """is_from_me 메시지의 sender 최빈값 = 내 카톡 표시 이름. 없으면 ''."""
    names = Counter(m.get("sender") for m in msgs
                    if m.get("is_from_me") and m.get("sender"))
    return names.most_common(1)[0][0] if names else ""


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


def extract_identity(client, model, all_msgs, display_name):
    """Opus로 대화에서 이 사람의 이름·별명(복수)과 프로필을 추출한다(톡방별로 다름)."""
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
        model=model,
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}],
        output_config={"format": {"type": "json_schema", "schema": IDENTITY_SCHEMA}},
    )
    text = next((b.text for b in resp.content if b.type == "text"), "")
    return json.loads(text)


def gather_my_messages(chat_id, sent_ids, fetch_limit):
    """톡방에서 내가 직접 친 텍스트 메시지(봇 발화 제외)와, 전체 타임라인을 반환."""
    msgs = kb.fetch_messages(chat_id, fetch_limit)
    sent = set(sent_ids)
    mine = [
        m for m in msgs
        if m.get("is_from_me")
        and m["id"] not in sent
        and m.get("type") in ("text", "unknown")
        and m.get("text")
    ]
    return msgs, mine


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


def build_pairs(all_msgs, mine_ids, max_pairs, window_sec=300):
    """직전 남의 메시지 → 내(직접 친) 답장 쌍."""
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
1. 길이 (평균/중앙값/분포)
2. 끊어 보내기(버스트) 경향
3. ㅋ/ㅎ/이모지 사용
4. 문장부호·띄어쓰기 습관
5. 자주 쓰는 종결 어미
6. 자주 쓰는 단어·표현
7. 대화 태도(먼저 말 걸기 vs 리액션, 받아치기 등)
8. 봇이 지켜야 할 출력 규칙(번호 목록)
""".strip()


def generate_style(client, model, stats, sample_texts):
    sample = "\n".join(f"- {t}" for t in sample_texts)
    stats_json = json.dumps(stats, ensure_ascii=False, indent=2)
    prompt = f"""다음은 한 사람이 카톡 단톡방에서 "직접 친" 메시지들의 통계와 표본이다.
이 사람의 말투를 관찰 가능한 구체적 특징으로 분석해 STYLE.md 를 작성하라.

{STYLE_SCHEMA_HINT}

[통계]
{stats_json}

[메시지 표본]
{sample}

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
    all_msgs, mine = gather_my_messages(chat_id, sent_ids, args.fetch_limit)
    if not mine:
        print(f"내가 직접 친 메시지를 찾지 못함 (target={target}).", file=sys.stderr)
        sys.exit(1)

    mine = mine[-args.my_messages:]
    mine_ids = [m["id"] for m in mine]
    texts = [m["text"] for m in mine]
    print(f"분석 대상: 내가 직접 친 메시지 {len(texts)}개 (봇 발화 {len(sent_ids)}개 제외)")

    stats = compute_stats(texts)
    client = kb.make_anthropic_client()

    # 이름·별명·프로필을 대화에서 추출(Opus). 톡방마다 호칭이 다르므로 톡방별로 저장한다.
    display_name = infer_name(all_msgs)  # sender 기반 표시이름(힌트·폴백)
    try:
        identity = extract_identity(client, args.model, all_msgs, display_name)
    except Exception as e:
        print(f"이름·프로필 LLM 추출 실패({e}) — 표시이름만 사용.", file=sys.stderr)
        identity = {"names": [display_name] if display_name else [],
                    "primary_name": display_name, "profile": ""}
    names = [n.strip() for n in identity.get("names", []) if n and n.strip()]
    primary = (args.name or identity.get("primary_name") or display_name or "").strip()
    if primary and primary not in names:
        names.insert(0, primary)
    names = list(dict.fromkeys(names))  # 중복 제거(순서 유지)
    profile = (identity.get("profile") or "").strip()

    cfgpath = os.path.join(room_dir, "config.env")
    if primary:
        kb.update_config_value(cfgpath, "BOT_NAME", primary)
    if names:
        kb.update_config_value(cfgpath, "BOT_ALIASES", ", ".join(names))
    print("봇 이름: " + (primary or "(미설정)")
          + (f" · 이름/별명: {', '.join(names)}" if names else "")
          + (" (직접 지정)" if args.name else " (대화에서 자동 추출)"))

    print("STYLE.md 생성 중...")
    style_body = generate_style(client, args.model, stats, texts[-120:])
    profile_section = "## 프로필 · 호칭 (자동 추출)\n\n"
    if names:
        profile_section += f"- 이 톡방에서 불리는 이름/별명: {', '.join(names)}\n"
    if profile:
        profile_section += f"\n{profile}\n"
    style_md = profile_section + "\n---\n\n" + style_body
    with open(style_path, "w", encoding="utf-8") as f:
        f.write(style_md + "\n")
    print(f"  → {style_path}")

    pairs = build_pairs(all_msgs, mine_ids, args.pairs)
    lines = [
        '# examples.txt — "직전 메시지 → 내 답장" 대화 쌍',
        "# (update_style.py 자동 생성. 봇 발화 제외, 내가 직접 친 답장만)",
        "",
    ]
    for sender, prev, reply in pairs:
        lines.append(f"상대> {prev}")
        lines.append(f"나> {reply}")
        lines.append("")
    with open(examples_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"  → {examples_path} ({len(pairs)} 쌍)")
    print("완료. 다음 봇 실행부터 갱신된 말투가 적용됩니다.")


if __name__ == "__main__":
    main()
