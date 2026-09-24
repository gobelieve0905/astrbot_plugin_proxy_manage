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
# Clash/Mihomo rule types.  Keep the normalized model deliberately close to
# Clash's public rule vocabulary so rules can be imported/exported without
# losing information.  Adapter-specific capability checks decide whether a
# selected core can actually render a given type.
RULE_TYPES=(
    'DOMAIN','DOMAIN-SUFFIX','DOMAIN-KEYWORD','DOMAIN-REGEX','GEOSITE','GEOIP',
    'DOMAIN-WILDCARD','SRC-GEOIP','IP-ASN','SRC-IP-ASN','IP-CIDR','IP-CIDR6','SRC-IP-CIDR',
    'IP-SUFFIX','SRC-IP-SUFFIX','SRC-PORT','DST-PORT','IN-PORT','DSCP',
    'PROCESS-NAME','PROCESS-NAME-WILDCARD','PROCESS-PATH','PROCESS-PATH-WILDCARD',
    'PROCESS-NAME-REGEX','PROCESS-PATH-REGEX','NETWORK','UID','IN-TYPE','IN-USER','IN-NAME',
    'REMATCH-NAME','SUB-RULE','RULE-SET',
    'AND','OR','NOT','MATCH'
)
RULE_TYPE_SET=set(RULE_TYPES)
# These Clash rule forms require separately managed rule-provider/sub-rule
# declarations. They remain recognizable in imported text, but this plugin's
# normalized model does not yet own those declarations, so Mihomo must reject
# them before configuration is written or applied.
RULE_TYPES_REQUIRING_DECLARATION={'RULE-SET','SUB-RULE'}
RULE_TYPE_ALIASES={
    'DOMAINSUFFIX':'DOMAIN-SUFFIX','DOMAIN_SUFFIX':'DOMAIN-SUFFIX',
    'DOMAINKEYWORD':'DOMAIN-KEYWORD','DOMAIN_KEYWORD':'DOMAIN-KEYWORD',
    'DOMAINREGEX':'DOMAIN-REGEX','DOMAIN_REGEX':'DOMAIN-REGEX',
    'SRCIPCIDR':'SRC-IP-CIDR','SRC_IP_CIDR':'SRC-IP-CIDR',
    'IPSUFFIX':'IP-SUFFIX','IP_SUFFIX':'IP-SUFFIX',
    'SRCIPSUFFIX':'SRC-IP-SUFFIX','SRC_IP_SUFFIX':'SRC-IP-SUFFIX',
    'SRCPORT':'SRC-PORT','SRC_PORT':'SRC-PORT','DSTPORT':'DST-PORT','DST_PORT':'DST-PORT',
    'INPORT':'IN-PORT','IN_PORT':'IN-PORT','PROCESSNAME':'PROCESS-NAME','PROCESS_NAME':'PROCESS-NAME',
    'PROCESSPATH':'PROCESS-PATH','PROCESS_PATH':'PROCESS-PATH',
    'PROCESSNAMEREGEX':'PROCESS-NAME-REGEX','PROCESS_NAME_REGEX':'PROCESS-NAME-REGEX',
    'PROCESSPATHREGEX':'PROCESS-PATH-REGEX','PROCESS_PATH_REGEX':'PROCESS-PATH-REGEX',
    'INTYPE':'IN-TYPE','IN_TYPE':'IN-TYPE','INUSER':'IN-USER','IN_USER':'IN-USER',
    'INNAME':'IN-NAME','IN_NAME':'IN-NAME','RULESET':'RULE-SET','RULE_SET':'RULE-SET',
}
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
