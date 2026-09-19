DIRECT = {"id":"direct","name":"直连","kernel_name":"DIRECT","mode":"direct","node_ids":[],"selected":"","enabled":True}
TEMPLATES = {
    "feishu":{"name":"飞书 / Lark","domains":[{"host":"open.feishu.cn","match":"exact"},{"host":"open.larksuite.com","match":"exact"},{"host":"feishu.cn","match":"suffix"},{"host":"larksuite.com","match":"suffix"}]},
    "telegram":{"name":"Telegram","domains":[{"host":"api.telegram.org","match":"exact"},{"host":"telegram.org","match":"suffix"},{"host":"t.me","match":"suffix"}]},
    "meta":{"name":"Meta","domains":[{"host":"graph.facebook.com","match":"exact"},{"host":"facebook.com","match":"suffix"},{"host":"fbcdn.net","match":"suffix"},{"host":"instagram.com","match":"suffix"}]},
    "github":{"name":"GitHub","hosts":["api.github.com","github.com","raw.githubusercontent.com"]},
}
HTTP_PROTOCOLS={'http','https','socks','socks5','socks5h'}
KINDS={"http","https","socks5","socks5h","mihomo","sing-box","xray"}
MODES={"direct","select","url-test","fallback"}
MATCHES={"exact","suffix"}
ADVANCED_SCHEMES={"ss","ssr","vmess","vless","trojan","hysteria","hysteria2","tuic","anytls"}
SUPPORTED_PROTOCOLS={'anytls','http','https','socks','socks5','socks5h'}
NOTICE_PATTERNS=(
    r'剩余流量|流量剩余|已用流量|套餐流量|traffic',
    r'距离.*重置|下次重置|重置剩余|reset',
    r'套餐到期|到期时间|有效期|过期时间|expire|expiry',
)
CONFIGURED='[configured]'
SENSITIVE_KEYS={
    'authorization','auth','password','passwd','secret','token','username','user','uuid','id',
    'api-key','api_key','apikey','client-id','client_id',
    'private-key','private_key','client-key','client_key','psk','credential','credentials',
}
REGIONS=[
    ("HK",r"香港|港|hk|hong\s*kong"), ("TW",r"台湾|臺灣|台|tw|taiwan"),
    ("JP",r"日本|日|jp|japan"), ("SG",r"新加坡|狮城|sg|singapore"),
    ("US",r"美国|美國|us|usa|united\s*states"), ("KR",r"韩国|韓國|kr|korea"),
    ("DE",r"德国|德國|de|germany"), ("UK",r"英国|英國|uk|britain"),
    ("MY",r"马来西亚|my|malaysia"), ("TH",r"泰国|th|thailand"),
    ("VN",r"越南|vn|vietnam"), ("PH",r"菲律宾|ph|philippines"),
    ("IN",r"印度|in|india"), ("AU",r"澳大利亚|澳洲|au|australia"),
]
