from app.useragent import parse


def test_parse():
    assert parse("Mozilla/5.0 (iPhone) AppleWebKit Mobile Safari") == ("Safari", "Mobile")
    assert parse("Mozilla/5.0 Chrome/120 Edg/120") == ("Edge", "Desktop")
    assert parse("Googlebot/2.1") == ("Bot", "Bot")
    assert parse(None) == ("Unknown", "Unknown")
