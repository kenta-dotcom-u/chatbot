import streamlit as st
from google import genai
from google.genai import types

# Show title and description.
st.title("💬 Chatbot")
st.write(
    "This is a simple chatbot that uses Google's Gemini model to generate responses. "
    "To use this app, you need to provide a Gemini API key, which you can get [here](https://aistudio.google.com/app/apikey). "
    "You can also learn how to build this app step by step by [following our tutorial](https://docs.streamlit.io/develop/tutorials/llms/build-conversational-apps)."
)

# Ask user for their Gemini API key via `st.text_input`.
gemini_api_key = st.text_input("Gemini API Key", type="password")
if not gemini_api_key:
    st.info("Please add your Gemini API key to continue.", icon="🗝️")
else:
    # 1. 新しい SDK の Client を初期化
    client = genai.Client(api_key=gemini_api_key.strip())
    MODEL_ID = "gemini-2.5-flash"  # 正しいモデル名を指定

    # Create a session state variable to store the chat messages.
    if "messages" not in st.session_state:
        st.session_state.messages = []

    # Display the existing chat messages via `st.chat_message`.
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    # Create a chat input field to allow the user to enter a message.
    if prompt := st.chat_input("What is up?"):

        # Store and display the current prompt.
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        # 2. 新しい SDK の形式（types.Content）で会話履歴を構築
        gemini_messages = [
            types.Content(
                role="user" if m["role"] == "user" else "model",
                parts=[types.Part.from_text(text=m["content"])],
            )
            for m in st.session_state.messages
        ]

        # Generate a response using the Gemini API.
        try:
            # 3. client.models.generate_content_stream を使用してストリーミング生成
            stream = client.models.generate_content_stream(
                model=MODEL_ID,
                contents=gemini_messages,
            )

            def stream_generator():
                for chunk in stream:
                    if chunk.text:
                        yield chunk.text

            with st.chat_message("assistant"):
                response = st.write_stream(stream_generator)
            st.session_state.messages.append({"role": "assistant", "content": response})

        except Exception as e:
            st.error(f"Error generating response ({MODEL_ID}): {e}")
