import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ui"))
from stream import parse_events, find_download_link, strip_presigned_links, parse_s3_uri

lines = [b'data: {"type": "tool", "data": "collect_inventory"}', b"", b": keep-alive",
         b'data: {"type": "text", "data": "Here is "}', 'data: "plain chunk"', b"data: not json",
         b'data: {"type": "error", "data": "boom"}', b'data: {"unrelated": 1}']
events = list(parse_events(lines))
assert events == [{"type": "tool", "data": "collect_inventory"}, {"type": "text", "data": "Here is "},
                  {"type": "text", "data": "plain chunk"}, {"type": "text", "data": "not json"},
                  {"type": "error", "data": "boom"}], events
url = "https://b.s3.amazonaws.com/diagrams/20261111-x.drawio?X-Amz-Signature=abc&X-Amz-Expires=3600"
assert find_download_link(f"Your diagram:\n{url}\n\nIt shows...") == url
assert find_download_link(f"[Download]({url}).") == url
assert find_download_link("no link here") is None
signed = "x https://b.s3.amazonaws.com/diagrams/a.drawio?AWSAccessKeyId=ASIA&Signature=abc%3D&Expires=1 y"
assert strip_presigned_links(signed) == "x (use the download button below) y"
assert strip_presigned_links("https://docs.aws.amazon.com/a.html") == "https://docs.aws.amazon.com/a.html"
assert parse_s3_uri("s3://bkt/diagrams/own-account/a.drawio") == ("bkt", "diagrams/own-account/a.drawio")
assert parse_s3_uri("s3://bkt/inventory/a.json") is None
print("ui stream: all tests passed")
