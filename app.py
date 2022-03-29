#!/usr/bin/env python
import streamlit as st
import requests
import pandas as pd

# Add title to the page.
st.title("FINANCE DASHBOARD")

if st.button(label='Select Data'):
    r = requests.get("https://gentle-caverns-79193.herokuapp.com/get_income_data")
    data = pd.DataFrame(r.json())
    st.dataframe(data)
    st.write("Data select successfully!")


# if __name__ == '__main__':
    # sys.argv = ["streamlit", "run", "app.py", "--server.port", str(os.environ["PORT"])]
    # sys.exit(stcli.main())
