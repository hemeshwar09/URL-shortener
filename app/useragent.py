"""Tiny dependency-free user-agent classifier (browser + device)."""


def parse(ua: str | None) -> tuple[str, str]:
    ua = (ua or "").lower()
    if not ua:
        return "Unknown", "Unknown"
    if any(k in ua for k in ("bot", "crawler", "spider", "slurp")):
        return "Bot", "Bot"
    if "ipad" in ua or "tablet" in ua:
        device = "Tablet"
    elif any(k in ua for k in ("mobile", "android", "iphone")):
        device = "Mobile"
    else:
        device = "Desktop"
    if "edg/" in ua:
        browser = "Edge"
    elif "opr/" in ua or "opera" in ua:
        browser = "Opera"
    elif "chrome" in ua or "crios" in ua:
        browser = "Chrome"
    elif "firefox" in ua or "fxios" in ua:
        browser = "Firefox"
    elif "safari" in ua:
        browser = "Safari"
    elif any(k in ua for k in ("curl", "python", "k6", "httpx")):
        browser = "Script"
    else:
        browser = "Other"
    return browser, device
