import re
import warnings
import requests
import pandas as pd
import streamlit as st
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed

warnings.filterwarnings("ignore")  # silence SSL-verify warnings

st.title("E-commerce Platform & Payment Detector")

uploaded_file = st.file_uploader("Upload TXT file with URLs (one per line)")
workers = st.slider("Parallel workers", 5, 50, 20)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# Order matters: more specific platforms first
PLATFORMS = {
    "Shopify": ["cdn.shopify.com", "/cdn/shopify/", "shopify.theme", "myshopify.com",
                "shopify-section", "shopify.shop", "shopify-payment-button"],
    "WooCommerce": ["wp-content/plugins/woocommerce", "wc-ajax", "wc_add_to_cart",
                    "woocommerce-"],
    "Magento": ["mage-init", "mage/cookies", "/static/frontend/", "x-magento",
                "magento_"],
    "BigCommerce": ["cdn11.bigcommerce.com", "bigcommerce.com"],
    "Salesforce Commerce Cloud": ["demandware", "/on/demandware.store"],
    "PrestaShop": ["prestashop", "/modules/ps_"],
    "OpenCart": ["catalog/view/theme", "route=product/product", "opencart"],
    "Shopware": ["shopware"],
    "Shift4Shop (3dcart)": ["3dcart", "shift4shop"],
    "Ecwid": ["ecwid"],
    "Squarespace Commerce": ["sqs-add-to-cart", "static1.squarespace.com", "squarespace-commerce"],
    "Wix Stores": ["wixstores", "wix-ecommerce", "_api/wix-ecommerce"],
    "Webflow Ecommerce": ["w-commerce"],
    "Square Online": ["square.site", "squareup.com/store"],
    "Dukaan": ["mydukaan.io"],
    "Zoho Commerce": ["zohocommerce", "zohoecommerce"],
}

GATEWAYS = [
    "js.stripe.com", "checkout.stripe.com", "paypal.com/sdk", "paypalobjects.com",
    "razorpay", "paytm", "payu", "cashfree", "ccavenue", "instamojo", "phonepe",
    "billdesk", "braintree", "adyen", "klarna", "afterpay", "affirm", "authorize.net",
    "2checkout", "mollie", "checkout.com", "shop_pay", "shop pay", "gokwik",
    "snapmint", "simpl", "apple pay", "google pay",
]

CART_SIGNALS = [
    "add to cart", "add-to-cart", "add_to_cart", "addtocart", "/cart/add",
    "add to bag", "buy now", "/checkout", "view cart", "shopping cart",
    "data-product-id", "woocommerce-loop-add-to-cart",
]


def normalize(u):
    u = u.strip().strip(",;\"'")
    if not re.match(r"^https?://", u, re.I):
        u = "https://" + u
    return u


def safe_get(url, timeout=12):
    """GET with SSL fallback. Returns (response, error_string)."""
    try:
        return requests.get(url, headers=HEADERS, timeout=timeout,
                            allow_redirects=True), None
    except requests.exceptions.SSLError:
        try:
            return requests.get(url, headers=HEADERS, timeout=timeout,
                                allow_redirects=True, verify=False), None
        except Exception as e:
            return None, f"SSL error: {type(e).__name__}"
    except requests.exceptions.Timeout:
        return None, "Timeout"
    except requests.exceptions.ConnectionError:
        return None, "Connection/DNS failed"
    except Exception as e:
        return None, type(e).__name__


def get_homepage(url):
    r, err = safe_get(url)
    if r is None and url.startswith("https://"):
        r, err2 = safe_get("http://" + url[len("https://"):])
        if r is not None:
            err = None
    return r, err


def json_or_none(resp):
    try:
        return resp.json()
    except Exception:
        return None


def check_shopify_api(base):
    """Return (is_shopify, sells_online, notes) using real Shopify JSON endpoints."""
    pr, _ = safe_get(f"{base}/products.json?limit=10")
    if pr is not None and pr.status_code == 200:
        data = json_or_none(pr)
        if isinstance(data, dict) and "products" in data:
            for p in data["products"]:
                for v in p.get("variants", []):
                    try:
                        if float(v.get("price") or 0) > 0 and v.get("available"):
                            return True, True, "priced, available products in /products.json"
                    except ValueError:
                        pass
            return True, False, "Shopify, but no priced/available products in feed"
    cr, _ = safe_get(f"{base}/cart.js")
    if cr is not None and cr.status_code == 200:
        data = json_or_none(cr)
        if isinstance(data, dict) and ("token" in data or "items" in data):
            return True, False, "/cart.js returned Shopify cart JSON"
    return False, False, ""


def check_site(raw):
    out = {"URL": raw, "Platform": "Unknown", "Online Payment": "Unknown", "Notes": ""}
    try:
        url = normalize(raw)
        r, err = get_homepage(url)

        if r is None:
            out["Platform"], out["Notes"] = "Error", err
            return out

        p = urlparse(r.url)
        base = f"{p.scheme}://{p.netloc}"
        blocked = r.status_code in (401, 403, 429, 503)
        html = r.text.lower() if not blocked or r.text else ""
        hdrs = " ".join(f"{k}:{v}" for k, v in r.headers.items()).lower()
        blob = html + " " + hdrs

        # ---------- Platform ----------
        platform = None
        for name, sigs in PLATFORMS.items():
            if any(s in blob for s in sigs):
                platform = name
                break

        # Shopify API check (also catches headless / blocked homepages)
        shop_api_sells, shop_notes = False, ""
        if platform in (None, "Shopify"):
            is_shop, shop_api_sells, shop_notes = check_shopify_api(base)
            if is_shop:
                platform = "Shopify"

        # ---------- Payment ----------
        password_wall = "/password" in r.url or "opening soon" in html
        has_cart = any(s in blob for s in CART_SIGNALS)
        gateways = [g for g in GATEWAYS if g in blob]

        if platform == "Shopify":
            if password_wall:
                pay, note = "No", "Store is password protected"
            elif shop_api_sells:
                pay, note = "Yes", shop_notes
            elif has_cart:
                pay, note = "Likely", "Cart/checkout signals on page"
            else:
                pay, note = "Unknown", shop_notes or "No clear selling signals"
        else:
            # Check /cart and /checkout pages if homepage had no signals
            if not has_cart and platform:
                for path in ("/cart", "/checkout"):
                    cr, _ = safe_get(base + path, timeout=8)
                    if cr is not None and cr.status_code == 200 and any(
                            s in cr.text.lower() for s in CART_SIGNALS + ["checkout", "your cart"]):
                        has_cart = True
                        break
            if has_cart and gateways:
                pay, note = "Yes", "Cart + gateway: " + ", ".join(gateways[:3])
            elif has_cart:
                pay, note = "Likely", "Cart/checkout found, gateway not visible"
            elif platform:
                pay, note = "Unknown", "Platform detected, no checkout signals"
            else:
                pay, note = "No", "No e-commerce signals found"

        if platform is None:
            platform = "Custom / Unknown e-commerce" if has_cart else "No e-commerce detected"

        if blocked:
            note = f"Homepage blocked (HTTP {r.status_code}). " + note

        out.update({"Platform": platform, "Online Payment": pay, "Notes": note})
    except Exception as e:
