import json
import time
import streamlit as st
from google import genai
from google.genai import types

# ページ設定
st.set_page_config(page_title="リアルタイム議事録メーカー (Gemini版)", page_icon="🎙️", layout="wide")
st.title("🎙️ リアルタイム議事録作成アプリ（Gemini版）")
st.caption("会議の録音・文字起こし・議題別の議事録＆ネクストアクション・質問への模範解答生成に加え、気になるキーワードをクリックして意味を調べられます。")

# セッション状態の初期化
if "transcript_history" not in st.session_state:
    st.session_state.transcript_history = []
if "minutes" not in st.session_state:
    st.session_state.minutes = "（まだ議事録はありません。音声を録音してください）"
if "keywords" not in st.session_state:
    st.session_state.keywords = []
if "term_explanations" not in st.session_state:
    st.session_state.term_explanations = {}
if "last_audio_bytes" not in st.session_state:
    st.session_state.last_audio_bytes = None

# サイドバー：APIキー設定とリセット
with st.sidebar:
    st.header("⚙️ 設定")
    default_api_key = st.secrets.get("GEMINI_API_KEY", "")
    api_key = st.text_input("Gemini API Key", value=default_api_key, type="password")
    
    # 混雑時に手動でも切り替えられるモデル選択
    primary_model = st.selectbox(
        "使用モデル（混雑時は自動で別モデルへ切替）",
        options=["gemini-3.8-flash", "gemini-3.8-flash-lite", "gemini-3.8-pro"],
        index=0,
    )
    
    st.divider()
    if st.button("🗑️ 議事録と履歴をすべてリセット", use_container_width=True):
        st.session_state.transcript_history = []
        st.session_state.minutes = "（まだ議事録はありません。音声を録音してください）"
        st.session_state.keywords = []
        st.session_state.term_explanations = {}
        st.session_state.last_audio_bytes = None
        st.rerun()

# APIキーのチェック
if not api_key:
    st.warning("👈 左のサイドバーに Gemini API Key を入力してください。（Google AI Studioで取得できます）")
    st.stop()

# Geminiクライアントの初期化
client = genai.Client(api_key=api_key)

# 503混雑エラー時に自動リトライ＆予備モデルへ切り替える共通関数
def generate_with_retry(contents, config, preferred_model: str):
    fallback_models = ["gemini-3.8-flash", "gemini-3.8-flash-lite", "gemini-3.8-pro"]
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
                    time.sleep(2 * (attempt + 1))
                    continue
                else:
                    break
    raise last_error

# 用語解説を取得する関数（キャッシュ機能付き）
def get_term_explanation(term: str, context: str) -> str:
    if term in st.session_state.term_explanations:
        return st.session_state.term_explanations[term]
    
    prompt = f"""
あなたは優秀なビジネスアシスタントです。
以下の【会議の文字起こし文脈】の中で使われている「{term}」という用語について、意味・背景・ポイントを分かりやすく簡潔に（3〜4行程度で）解説してください。
一般的な意味だけでなく、この会議の文脈における使われ方も踏まえて教えてください。

【会議の文字起こし文脈】
{context if context else "（まだ文脈はありません）"}
"""
    response = generate_with_retry(
        contents=prompt,
        config=types.GenerateContentConfig(temperature=0.3),
        preferred_model=primary_model,
    )
    explanation = response.text
    st.session_state.term_explanations[term] = explanation
    return explanation

# メイン画面：音声入力エリア
st.subheader("1. 音声の録音")
audio_value = st.audio_input("マイクボタンを押して発言を録音（停止すると自動処理されます）")

# 新しい音声が録音された場合の処理
if audio_value is not None:
    audio_bytes = audio_value.getvalue()
    
    if audio_bytes != st.session_state.last_audio_bytes:
        with st.spinner("Geminiが音声を解析し、議事録・ネクストアクション・模範解答を作成中..."):
            try:
                past_transcripts = "\n".join(st.session_state.transcript_history)
                
                prompt = f"""
あなたは優秀な書記兼ビジネスコンサルタントです。
添付された【新しい音声データ】を正確に日本語で文字起こしし、以下の3点を行ってください。
1. 新しい音声データの正確な文字起こし
2. 【これまでの発言履歴】と統合した最新の議事録の作成。議題ごとに要約・決定事項・ネクストアクションを整理した上で、さらに「ネクストアクションをスムーズに進めるための具体的な進め方・たたき台（模範解答）」と「会議内で出た質問・疑問点・未解決事項に対する回答案（模範解答）」も併記すること。
3. 今回の発言から、参加者が意味を調べたくなるような専門用語・ビジネス用語・固有名詞・略語（キーワード）を最大5個抽出

【これまでの発言履歴】
{past_transcripts if past_transcripts else "（なし・これが最初の発言です）"}

【議事録の出力フォーマット（Markdown形式）】
## 📋 議題別の詳細・ネクストアクション・模範解答

### 議題1：（会話から推測される議題タイトル）
- **📝 議論の要約:** （話された内容の要点）
- **📌 決定事項:** （決まったこと。なければ「継続議論」）
- **🚀 この議題のネクストアクション:** 
  - [ ] （具体的な行動内容 / 担当：〇〇 / 期限：〇〇 ※会話から読み取れる範囲で補記）
- **💡 ネクストアクションの進め方・たたき台（模範解答）:**
  - （上記のネクストアクションを実行する際の具体的な手順、確認すべきポイント、メール文面や資料構成案などの実践的な模範解答）
- **❓ 質問・疑問点への回答案（模範解答）:**
  - **Q:** （会議中で出た質問・懸念点・持ち帰り事項。質問がなければ「特になし」）
  - **A（模範解答）:** （その質問に対する論理的で分かりやすい回答例・解決策）

（※複数の議題がある場合は「### 議題2：〜」と続けて整理してください）

---

## ✅ 全体のネクストアクション一覧（まとめ）
- [ ] 【議題1】アクション内容（担当・期限）
"""
                response = generate_with_retry(
                    contents=[
                        types.Part.from_bytes(
                            data=audio_bytes,
                            mime_type="audio/wav",
                        ),
                        prompt,
                    ],
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema={
                            "type": "OBJECT",
                            "properties": {
                                "new_transcript": {
                                    "type": "STRING",
                                    "description": "今回添付された音声データのみの正確な日本語文字起こし",
                                },
                                "updated_minutes": {
                                    "type": "STRING",
                                    "description": "これまでの履歴と今回の内容を統合した最新の議事録（議題ごとのネクストアクション・質問事項への模範解答を含むMarkdown形式）",
                                },
                                "extracted_keywords": {
                                    "type": "ARRAY",
                                    "items": {"type": "STRING"},
                                    "description": "今回の発言に含まれる専門用語・固有名詞・略語などのキーワードリスト",
                                },
                            },
                            "required": ["new_transcript", "updated_minutes", "extracted_keywords"],
                        },
                        temperature=0.25,
                    ),
                    preferred_model=primary_model,
                )
                
                result = json.loads(response.text)
                st.session_state.transcript_history.append(result["new_transcript"])
                st.session_state.minutes = result["updated_minutes"]
                st.session_state.last_audio_bytes = audio_bytes
                
                # 重複を除いてキーワードを追加
                for kw in result.get("extracted_keywords", []):
                    if kw and kw not in st.session_state.keywords:
                        st.session_state.keywords.append(kw)
                
                st.success("議事録・模範解答・キーワードを更新しました！")
                
            except Exception as e:
                st.error(f"サーバーが大変混雑しているため処理できませんでした。数秒おいて再度お試しください。({e})")

st.divider()

# 結果表示エリア（2カラム構成）
col1, col2 = st.columns([3, 2])

with col1:
    st.subheader("2. 生成された議事録 ＆ 模範解答（リアルタイム更新）")
    st.markdown(st.session_state.minutes)
    
    st.download_button(
        label="📥 議事録をMarkdownでダウンロード",
        data=st.session_state.minutes,
        file_name="meeting_minutes.md",
        mime="text/markdown"
    )

with col2:
    st.subheader("3. 文字起こしログ ＆ 用語検索")
    
    # --- 気になる言葉のクリック検索エリア ---
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
            
        # リストにない言葉も手動で調べられる入力欄
        custom_term = st.text_input(
            "リストにない言葉を直接入力して検索",
            placeholder="例：KPI、アジャイル、リスケ など",
            label_visibility="collapsed"
        )
        
        # クリックされた単語または手入力された単語の解説を表示
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

    # --- 文字起こしログの表示 ---
    if st.session_state.transcript_history:
        for i, text in enumerate(st.session_state.transcript_history, 1):
            st.info(f"**発言 {i}:** {text}")
    else:
        st.write("録音されたテキストがここに蓄積されます。")
