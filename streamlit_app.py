import datetime
import json
import re
import time
import streamlit as st
from google import genai
from google.genai import types

# ページ設定
st.set_page_config(page_title="リアルタイム議事録メーカー (Gemini版)", page_icon="🎙️", layout="wide")
st.title("🎙️ リアルタイム議事録作成アプリ（Gemini版）")
st.caption("会議の録音・文字起こし・議題別の議事録＆ネクストアクション・質問への模範解答生成に加え、過去の会議の保存・再開やキーワード解説ができます。")

def extract_meeting_title(minutes_text: str) -> str:
    match = re.search(r"###\s*議題1[：:]\s*(.+)", minutes_text)
    if match:
        title = match.group(1).strip()
        return title[:25] + ("..." if len(title) > 25 else "")
    return "無題の会議"

def archive_current_meeting():
    if not st.session_state.transcript_history:
        return

    now_str = datetime.datetime.now().strftime("%Y/%m/%d %H:%M")
    title = extract_meeting_title(st.session_state.minutes)
    
    meeting_record = {
        "id": st.session_state.current_meeting_id,
        "title": title,
        "updated_at": now_str,
        "transcript_history": list(st.session_state.transcript_history),
        "minutes": st.session_state.minutes,
        "keywords": list(st.session_state.keywords),
        "term_explanations": dict(st.session_state.term_explanations),
    }

    existing_idx = next(
        (i for i, m in enumerate(st.session_state.archived_meetings) if m["id"] == meeting_record["id"]),
        None
    )
    if existing_idx is not None:
        st.session_state.archived_meetings[existing_idx] = meeting_record
    else:
        st.session_state.archived_meetings.insert(0, meeting_record)

def start_new_meeting(save_current: bool = True):
    if save_current:
        archive_current_meeting()
    
    st.session_state.current_meeting_id = datetime.datetime.now().strftime("%Y%m%d%H%M%S%f")
    st.session_state.transcript_history = []
    st.session_state.minutes = "（まだ議事録はありません。音声を録音してください）"
    st.session_state.keywords = []
    st.session_state.term_explanations = {}
    st.session_state.last_audio_bytes = None
    st.session_state.audio_input_key = st.session_state.get("audio_input_key", 0) + 1

def load_archived_meeting(meeting_id: str):
    archive_current_meeting()
    
    target = next((m for m in st.session_state.archived_meetings if m["id"] == meeting_id), None)
    if target:
        st.session_state.current_meeting_id = target["id"]
        st.session_state.transcript_history = list(target["transcript_history"])
        st.session_state.minutes = target["minutes"]
        st.session_state.keywords = list(target["keywords"])
        st.session_state.term_explanations = dict(target["term_explanations"])
        st.session_state.last_audio_bytes = None
        st.session_state.audio_input_key = st.session_state.get("audio_input_key", 0) + 1

# セッション状態の初期化
if "archived_meetings" not in st.session_state:
    st.session_state.archived_meetings = []
if "transcript_history" not in st.session_state or "audio_input_key" not in st.session_state or "current_meeting_id" not in st.session_state:
    start_new_meeting(save_current=False)

# サイドバー：APIキー設定とリセット
with st.sidebar:
    st.header("⚙️ 設定")
    default_api_key = st.secrets.get("GEMINI_API_KEY", "")
    api_key = st.text_input("Gemini API Key", value=default_api_key, type="password")
    
    primary_model = st.selectbox(
        "使用モデル（liteが最も高速です）",
        options=["gemini-3.8-flash-lite", "gemini-3.8-flash", "gemini-3.8-pro"],
        index=0,
    )
    
    st.divider()
    if st.button("🔄 新しい会議を始める（現在の内容を保存）", key="sidebar_reset", use_container_width=True):
        start_new_meeting(save_current=True)
        st.rerun()

if not api_key:
    st.warning("👈 左のサイドバーに Gemini API Key を入力してください。（Google AI Studioで取得できます）")
    st.stop()

client = genai.Client(api_key=api_key)

# 自動リトライ付き生成関数
def generate_with_retry(contents, config, preferred_model: str):
    fallback_models = ["gemini-3.8-flash-lite", "gemini-3.8-flash", "gemini-3.8-pro"]
    models_to_try = [preferred_model] + [m for m in fallback_models if m != preferred_model]
    
    last_error = None
    for model_name in models_to_try:
        for attempt in range(2):
            try:
                return client.models.generate_content(
                    model=model_name,
                    contents=contents,
                    config=config,
                )
            except Exception as e:
                last_error = e
                error_str = str(e)
                if "503" in error_str or "UNAVAILABLE" in error_str or "429" in error_str:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                else:
                    break
    raise last_error

def get_term_explanation(term: str, context: str) -> str:
    if term in st.session_state.term_explanations:
        return st.session_state.term_explanations[term]
    
    prompt = f"""
以下の【会議の文字起こし文脈】の中で使われている「{term}」の意味・背景を3行以内で簡潔に解説してください。

【会議の文字起こし文脈】
{context if context else "（なし）"}
"""
    response = generate_with_retry(
        contents=prompt,
        config=types.GenerateContentConfig(temperature=0.3),
        preferred_model=primary_model,
    )
    explanation = (response.text or "解説を取得できませんでした。").strip()
    st.session_state.term_explanations[term] = explanation
    return explanation

# メイン画面：音声入力エリア
st.subheader("1. 音声の録音")
audio_value = st.audio_input(
    "マイクボタンを押して発言を録音（1〜2分ごとに区切ると高速に動作します）",
    key=f"audio_recorder_{st.session_state.audio_input_key}"
)

# 新しい音声が録音された場合の2段階高速処理
if audio_value is not None:
    audio_bytes = audio_value.getvalue()
    
    if audio_bytes != st.session_state.last_audio_bytes:
        with st.status("🚀 高速解析中...", expanded=True) as status:
            try:
                # 【ステップ1】音声の文字起こしを最速で実行
                st.write("① 音声を文字起こし中...")
                transcribe_prompt = """
添付された音声データを正確に日本語で文字起こししてください。
- 話者の区別は不要です。自然な日本語の文章として書き起こしてください。
- 余計な挨拶や説明は一切省き、文字起こし結果のみを出力してください。
"""
                transcribe_res = generate_with_retry(
                    contents=[
                        types.Part.from_bytes(data=audio_bytes, mime_type="audio/wav"),
                        transcribe_prompt,
                    ],
                    config=types.GenerateContentConfig(temperature=0.1),
                    preferred_model=primary_model,
                )
                
                new_transcript = (transcribe_res.text or "").strip()
                if not new_transcript:
                    new_transcript = "（音声を認識できませんでした。無音または雑音のみの可能性があります）"
                
                st.session_state.transcript_history.append(new_transcript)
                st.session_state.last_audio_bytes = audio_bytes
                
                # 【ステップ2】テキストのみで議事録・模範解答・キーワードを生成
                st.write("② 議事録・模範解答・キーワードを生成中...")
                full_transcripts = "\n".join(st.session_state.transcript_history)
                minutes_prompt = f"""
あなたは優秀な書記兼ビジネスコンサルタントです。
以下の【会議の文字起こし履歴】をもとに、最新の議事録とキーワードを作成してください。

【会議の文字起こし履歴】
{full_transcripts}

【議事録の出力フォーマット（Markdown形式）】
## 📋 議題別の詳細・ネクストアクション・模範解答

### 議題1：（会話から推測される議題タイトル）
- **📝 議論の要約:** （話された内容の要点を簡潔に箇条書き）
- **📌 決定事項:** （決まったこと。なければ「継続議論」）
- **🚀 この議題のネクストアクション:** 
  - [ ] （具体的な行動内容 / 担当：〇〇 / 期限：〇〇 ※会話から読み取れる範囲で補記）
- **💡 ネクストアクションの進め方・たたき台（模範解答）:**
  - （実行する際の具体的な手順やたたき台を簡潔に記載）
- **❓ 質問・疑問点への回答案（模範解答）:**
  - **Q:** （会議中で出た質問・懸念点。なければ「特になし」）
  - **A（模範解答）:** （簡潔で論理的な回答例）

（※複数の議題がある場合は「### 議題2：〜」と続けて整理）

---

## ✅ 全体のネクストアクション一覧（まとめ）
- [ ] 【議題1】アクション内容（担当・期限）
"""
                minutes_res = generate_with_retry(
                    contents=minutes_prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema={
                            "type": "OBJECT",
                            "properties": {
                                "updated_minutes": {
                                    "type": "STRING",
                                    "description": "最新の議事録（Markdown形式）",
                                },
                                "extracted_keywords": {
                                    "type": "ARRAY",
                                    "items": {"type": "STRING"},
                                    "description": "専門用語・固有名詞・略語（最大5個）",
                                },
                            },
                            "required": ["updated_minutes", "extracted_keywords"],
                        },
                        temperature=0.2,
                    ),
                    preferred_model=primary_model,
                )
                
                if not minutes_res.text:
                    raise ValueError("AIからの応答が空でした。もう一度お試しください。")
                
                result = json.loads(minutes_res.text)
                st.session_state.minutes = result["updated_minutes"]
                
                for kw in result.get("extracted_keywords", []):
                    if kw and kw not in st.session_state.keywords:
                        st.session_state.keywords.append(kw)
                
                status.update(label="✅ 完了しました！", state="complete", expanded=False)
                
            except Exception as e:
                status.update(label="⚠️ 処理中にエラーが発生しました", state="error")
                st.error(f"エラー詳細: {e}")

st.divider()

# 結果表示エリア（2カラム構成）
col1, col2 = st.columns([3, 2])

# 左カラム：議事録 ＆ 履歴表示
with col1:
    st.subheader("2. 生成された議事録 ＆ 模範解答（リアルタイム更新）")
    st.markdown(st.session_state.minutes)
    
    st.write("")
    
    btn_col1, btn_col2 = st.columns(2)
    with btn_col1:
        st.download_button(
            label="📥 議事録をMarkdownでダウンロード",
            data=st.session_state.minutes,
            file_name="meeting_minutes.md",
            mime="text/markdown",
            use_container_width=True
        )
    with btn_col2:
        if st.button("🔄 新しい会議を始める（現在の内容を保存）", key="main_reset_btn", use_container_width=True):
            start_new_meeting(save_current=True)
            st.rerun()

    if st.session_state.archived_meetings:
        st.write("")
        with st.container(border=True):
            st.markdown("📂 **過去の会議履歴（クリックすると途中から再開できます）**")
            for past in st.session_state.archived_meetings:
                is_current = (past["id"] == st.session_state.current_meeting_id)
                label_prefix = "🟢 【編集中】" if is_current else "📄 再開する："
                btn_label = f"{label_prefix} {past['title']}（{past['updated_at']} / 録音{len(past['transcript_history'])}回）"
                
                hist_col1, hist_col2 = st.columns([5, 1])
                with hist_col1:
                    if st.button(
                        btn_label,
                        key=f"load_{past['id']}",
                        disabled=is_current,
                        use_container_width=True
                    ):
                        load_archived_meeting(past["id"])
                        st.rerun()
                with hist_col2:
                    if st.button("🗑️", key=f"del_{past['id']}", help="この履歴を削除"):
                        st.session_state.archived_meetings = [
                            m for m in st.session_state.archived_meetings if m["id"] != past["id"]
                        ]
                        st.rerun()

# 右カラム：文字起こしログ ＆ 用語検索（部分更新フラグメント化）
@st.fragment
def render_right_column():
    st.subheader("3. 文字起こしログ ＆ 用語検索")
    
    with st.container(border=True):
        st.markdown("🔍 **気になる言葉をクリックして意味を調べる**")
        
        selected_term = None
        if st.session_state.keywords:
            selected_term = st.pills(
                "抽出されたキーワード（クリックで解説表示）",
                options=st.session_state.keywords,
                selection_mode="single",
            )
        else:
            st.caption("音声を録音すると、ここに重要な用語がボタンとして並びます。")
            
        custom_term = st.text_input(
            "リストにない言葉を直接入力して検索",
            placeholder="例：KPI、アジャイル、リスケ など",
            label_visibility="collapsed"
        )
        
        target_term = custom_term.strip() if custom_term.strip() else selected_term
        
        if target_term:
            with st.spinner(f"「{target_term}」の意味を検索中..."):
                try:
                    full_context = "\n".join(st.session_state.transcript_history)
                    explanation = get_term_explanation(target_term, full_context)
                    st.success(f"**📖 {target_term}**\n\n{explanation}")
                except Exception as e:
                    st.error(f"検索中にエラーが発生しました: {e}")

    st.write("")

    if st.session_state.transcript_history:
        for i, text in enumerate(st.session_state.transcript_history, 1):
            st.info(f"**録音 {i}:**\n\n{text}")
    else:
        st.write("録音されたテキストがここに蓄積されます。")

with col2:
    render_right_column()
