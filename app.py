#!/usr/bin/env python
from summa import summarizer
import streamlit as st
# Add title to the page.
st.title("Text summarization")
# Ask user for input text.
input_sent = st.text_area("Input Text", "", height=400)
# User input on what fraction of the original text to return.
ratio = st.slider(
    "Summarization fraction",
    min_value=0.0,
    max_value=1.0,
    value=0.2,
    step=0.01)
# Summarize the original text.
# Print out the results.
st.write(input_sent)