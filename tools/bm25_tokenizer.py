# -*- coding: utf-8 -*-
"""BM25 分词器：jieba 精确模式 + 领域词典 + 条款号/数字通道。

设计要点
--------
1. **索引与查询必须共用同一个 tokenizer**，否则线上永远查不到（token 对不上）。
2. 词典从文档 metadata 自动生成（指标名、发文机关、章节、文档标题、《》制度名），
   只保留 50 条人工种子术语，避免维护一张几千词的表。
3. 词典落盘为 txt（一行一词），并计算 hash 写进索引，用于检测「词典与索引不一致」。
4. jieba 不可用时自动降级为正则切分，并在日志里明确提示，保证服务不挂。
5. 条款号（第X条）单独作为 token，数字/百分比/英文缩写按整词保留。

环境变量
--------
BM25_TOKENIZER   jieba（默认）| regex
BM25_DICT_PATH   词典路径，默认 bm25_domain_dict.txt
"""
import os
import re
import hashlib

TOKENIZER_VERSION = "jieba-v1"

DICT_PATH = os.getenv("BM25_DICT_PATH", "bm25_domain_dict.txt")
BACKEND_ENV = os.getenv("BM25_TOKENIZER", "jieba").strip().lower()

CLAUSE_RE = re.compile(r"第[一二三四五六七八九十百千0-9]+条")
TOKEN_RE = re.compile(r"[\u4e00-\u9fa5a-zA-Z0-9]+")
ASCII_BAD = re.compile(r"[\"'=,;:\[\]{}()\\/<>|@#$%^&*~`_]")
SENT_PUNCT = re.compile(r"[。，；：、（）《》【】]")

# 只补 jieba 默认词表里没有、且一旦被切碎就会导致召回失败的监管术语。
SEED_TERMS = [
    "资本充足率", "核心一级资本充足率", "一级资本充足率", "流动性覆盖率", "净稳定资金比例",
    "杠杆率", "不良贷款率", "拨备覆盖率", "贷款拨备率", "风险加权资产", "信用风险", "市场风险",
    "操作风险", "流动性风险", "大额风险暴露", "存款准备金率", "同业拆借", "理财子公司",
    "表外业务", "影子银行", "绿色信贷", "普惠型小微企业贷款", "涉农贷款", "资本工具",
    "其他一级资本", "二级资本", "损失吸收能力", "系统重要性银行", "恢复计划", "处置计划",
    "压力测试", "净息差", "成本收入比", "资产利润率", "资本利润率", "流动性比例",
    "单一客户集中度", "关联交易", "内部评级法", "权重法", "违约概率", "违约损失率",
    "信用风险缓释", "资产证券化", "押品管理", "并表管理", "信息科技风险", "银行函证",
    "数据安全事件", "监管统计",
]

_jieba = None
_ready = False
_backend = "regex"
_words = set()
_dict_hash = "none"
_warned = set()


# ---------------- 词典构建 ----------------

def _clean_word(w):
    w = str(w or "").strip()
    if not (2 <= len(w) <= 40):
        return None
    if ASCII_BAD.search(w):
        return None
    if re.fullmatch(r"[\d.\s%]+", w):
        return None
    if len(w) > 16 and not re.search(r"[\u4e00-\u9fa5]", w):
        return None
    return w


def _clean_term(w):
    w = _clean_word(w)
    if not w or len(w) > 16 or SENT_PUNCT.search(w):
        return None
    return w


def build_domain_dict(metadata):
    """从 metadata 自动生成词典，返回 (词表, 各来源计数)。"""
    words = set(SEED_TERMS)
    stats = {"seed": len(SEED_TERMS)}
    for m in metadata or []:
        if not isinstance(m, dict):
            continue
        for key in ("indicator", "authority", "chapter", "section"):
            v = _clean_term(m.get(key))
            if v:
                words.add(v)
                stats[key] = stats.get(key, 0) + 1
        for tag in (m.get("tags") or []):
            v = _clean_term(tag)
            if v:
                words.add(v)
                stats["tags"] = stats.get("tags", 0) + 1
        fn = str(m.get("file_name") or "")
        fn = re.sub(r"^\d+_", "", fn)
        fn = re.sub(r"\.(docx?|pdf|xlsx?|xls)$", "", fn, flags=re.I)
        for name in re.findall(r"《([^》]{2,40})》", fn):
            v = _clean_word(name)
            if v:
                words.add(v)
                stats["title_book"] = stats.get("title_book", 0) + 1
        v = _clean_word(fn.replace("_", "").replace(" ", ""))
        if v:
            words.add(v)
            stats["file_title"] = stats.get("file_title", 0) + 1
    return words, stats


def dict_hash(words):
    joined = "\n".join(sorted(words))
    return hashlib.sha1(joined.encode("utf-8")).hexdigest()[:12]


def save_dict(words, path=DICT_PATH):
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(sorted(words)))
    return path


def _load_dict_file(path=DICT_PATH):
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return {line.strip() for line in f if line.strip() and not line.startswith("#")}


# ---------------- 初始化 ----------------

def init(metadata=None, dict_path=None, rebuild_dict=False, verbose=True):
    """初始化分词器。

    metadata 给了且词典缺失（或 rebuild_dict=True）时，会用 metadata 生成并落盘。
    返回 backend 名称。
    """
    global _jieba, _ready, _backend, _words, _dict_hash

    if _ready and not rebuild_dict:
        return _backend

    path = dict_path or DICT_PATH

    if BACKEND_ENV == "regex":
        _backend = "regex"
        _ready = True
        if verbose:
            print("[BM25Tokenizer] BM25_TOKENIZER=regex，使用正则切分（旧行为）")
        return _backend

    try:
        import jieba
    except ImportError:
        _backend = "regex"
        _ready = True
        if "jieba" not in _warned:
            _warned.add("jieba")
            print("[BM25Tokenizer] 未安装 jieba，降级为正则切分；pip install jieba 可启用领域分词")
        return _backend

    _jieba = jieba
    jieba.initialize()

    words = None if rebuild_dict else _load_dict_file(path)
    if words is None and metadata:
        words, stats = build_domain_dict(metadata)
        try:
            save_dict(words, path)
            if verbose:
                detail = "、".join(f"{k}:{v}" for k, v in stats.items())
                print(f"[BM25Tokenizer] 生成领域词典 {len(words)} 词 → {path}（{detail}）")
        except OSError as e:
            print(f"[BM25Tokenizer] 词典保存失败（{e}），本次仅在内存生效")
    if words is None:
        words = set(SEED_TERMS)
        if verbose:
            print(f"[BM25Tokenizer] 未找到 {path}，仅使用 {len(words)} 条种子术语")
    else:
        words |= set(SEED_TERMS)

    for w in words:
        jieba.add_word(w)

    _words = words
    _dict_hash = dict_hash(words)
    _backend = "jieba"
    _ready = True
    if verbose:
        print(f"[BM25Tokenizer] backend=jieba，词典 {len(_words)} 词，hash={_dict_hash}")
    return _backend


def signature():
    """写进索引，用于检测「词典/分词器与索引不一致」。"""
    return {"version": TOKENIZER_VERSION, "backend": _backend, "dict_hash": _dict_hash,
            "dict_size": len(_words)}


# ---------------- 切词 ----------------

def tokenize(text):
    if not _ready:
        init()

    text = (text or "").lower()
    if _backend != "jieba":
        return TOKEN_RE.findall(text)

    clauses = CLAUSE_RE.findall(text)
    tokens = []
    for t in _jieba.lcut(text, cut_all=False):
        t = t.strip()
        if not t or not re.search(r"[\u4e00-\u9fa5a-zA-Z0-9]", t):
            continue
        tokens.append(t)
    tokens.extend(clauses)      # 条款号单独成 token
    return tokens
