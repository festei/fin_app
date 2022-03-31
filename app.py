#!/usr/bin/env python
import streamlit as st
import requests
import pandas as pd
import numpy as np

companies = ['IBM', 'Allianz']

with st.sidebar:
    st.title("DASHBOARD")

    add_selectbox = st.sidebar.multiselect(
        "Please choose a company", companies
    )

    if st.button(label='Select Data'):
        r = requests.get("https://gentle-caverns-79193.herokuapp.com/get_income_data")
        data = pd.DataFrame(r.json())
        data = data[['ebit']]

columns = st.columns([3,1])

with columns[0]:
    st.header("Visualization")
    data_viz = np.random.randn(10, 1)
    st.line_chart(data_viz)

with columns[1]:
    st.header("Data")
    try:
        st.dataframe(data)
    except:
        print("Please select a company")


# if __name__ == '__main__':
    # sys.argv = ["streamlit", "run", "app.py", "--server.port", str(os.environ["PORT"])]
    # sys.exit(stcli.main())
