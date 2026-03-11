import streamlit as st
import requests
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed

st.title("Shopify Store Detector (Advanced)")

uploaded_file = st.file_uploader("Upload TXT file with URLs")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
}

shopify_signals = [
    "cdn.shopify.com",
    "/cdn/shopify/",
    "shopify-section",
    "shopify.theme",
    "myshopify.com",
    "x-shopify"
]

def detect_shopify(url):

    try:
        if not url.startswith("http"):
            url = "https://" + url

        r = requests.get(url, headers=HEADERS, timeout=8)

        html = r.text.lower()

        for signal in shopify_signals:
            if signal in html:
                return "Shopify"

        # strong signal check
        test1 = requests.get(url.rstrip("/") + "/products.json",
                             headers=HEADERS, timeout=8)

        if test1.status_code == 200:
            return "Shopify"

        test2 = requests.get(url.rstrip("/") + "/cart.js",
                             headers=HEADERS, timeout=8)

        if test2.status_code == 200:
            return "Shopify"

        return "Not Shopify"

    except:
        return "Error"


if uploaded_file:

    text = uploaded_file.read().decode("utf-8")
    urls = [x.strip() for x in text.split("\n") if x.strip()]

    results = []

    progress = st.progress(0)

    total = len(urls)

    with ThreadPoolExecutor(max_workers=50) as executor:

        futures = {executor.submit(detect_shopify, url): url for url in urls}

        completed = 0

        for future in as_completed(futures):

            url = futures[future]

            try:
                result = future.result()
            except:
                result = "Error"

            results.append([url, result])

            completed += 1
            progress.progress(completed / total)

    df = pd.DataFrame(results, columns=["URL", "Platform"])

    st.dataframe(df)

    csv = df.to_csv(index=False).encode("utf-8")

    st.download_button(
        "Download Results",
        csv,
        "shopify_results.csv",
        "text/csv"
    )