# 两层默认 salt 与过期契约的现状核对

只记录现状，不改任何实现。复现：`PYTHONPATH=src .venv/bin/python repro/default-salt.py`（密钥统一为 `b"shared-secret"`）。行号均指向当前工作树的 `src/itsdangerous/`。

## A 段：两个“默认 salt”各是什么，salt 到底有没有被忽略

两个字面量，字节数不同，不是同一个东西：

| 层 | 字面量 | 字节数 | 精确字节（十进制） | 定义位置 |
|---|---|---|---|---|
| 签名层 `Signer` | `b'itsdangerous.Signer'` | 19 | `[105, 116, 115, 100, 97, 110, 103, 101, 114, 111, 117, 115, 46, 83, 105, 103, 110, 101, 114]`（hex `69747364616e6765726f75732e5369676e6572`） | `signer.py:132` 形参默认值；`signer.py:153-156` 在传入 `None` 时再次补成同一个值 |
| 序列化层 `Serializer` | `b'itsdangerous'` | 12 | `[105, 116, 115, 100, 97, 110, 103, 101, 114, 111, 117, 115]`（hex `69747364616e6765726f75732e`） | `serializer.py:195` 实现签名（4 个 `@t.overload` 签名里同样出现：`serializer.py:113/129/145/164/180`） |

前 12 个字节完全重合，`Signer` 的默认值只是序列化层默认值再拼一个 `.Signer`。

默认那套 key 派生离不开 salt：`Signer.default_key_derivation == 'django-concat'`（`signer.py:127`，复现 A 段原样打印），`derive_key()` 在该分支算的是 `sha1(self.salt + b"signer" + secret_key).digest()`（`signer.py:202-205`）。salt 是哈希输入的第一段，不参与派生的唯一分支是显式 `key_derivation="none"`（`signer.py:209`，直接返回 `secret_key`，实测该分支换 salt 派生结果不变）。同一把 `b"shared-secret"` 在默认派生下两个 salt 派生出的 key 就不同：

- salt `b'itsdangerous.Signer'` → `5674ce6725d7f49727e8959dc61efbeba87405d0`
- salt `b'itsdangerous'` → `fbe210884e71abb7595703cbcaa2b83d0ee8ed35`

所以 A 段读数 `salt ignored under default derivation? False`：`Signer(k).sign(b"x")` 得 `b'x.S02hjWZfUMStaXOSyEByGR_F4Gw'`，换成 `salt=b"totally-different"` 得 `b'x.rMa5evzeMYgrE3AwBQXxGtk40Ao'`，签名不同。salt 在默认派生里被实际使用，没有被忽略。

## B 段：同一个 token，两个默认 salt 谁验得过

复现 B 段把序列化器固定为 `signer_kwargs={"key_derivation": "hmac"}`，产出 token（稳定可复现）：

```
{"u": 1}.CcZd1RqEih7ZoQu-jziX66Hfjps
```

载荷与签名以最后一个 `.` 切分：payload `{"u": 1}`，sig `CcZd1RqEih7ZoQu-jziX66Hfjps`。脚本里两个验签入口的读数：

- `Signer(k, key_derivation="hmac")`（签名层默认 salt `b'itsdangerous.Signer'`）→ **`False`，验不过**。
- `Signer(k, key_derivation="hmac", salt=ser.salt)`（序列化层默认 salt `b'itsdangerous'`）→ **`True`，验得过**。

即：要验过这个 token，salt 必须显式改成 `b'itsdangerous'`，对应构造是 `Signer(b"shared-secret", key_derivation="hmac", salt=b"itsdangerous")`。hmac 分支下两个 salt 各自的派生 key 分别是 `1f94cd2492103679ea0728ee52dfc3dc7b3e0777`（签名层默认）和 `d542ce189fe46ffabdbe2b769b6b422083853beb`（序列化层默认），自然只有后者能对上。默认的 `django-concat` 同理，裸 `Signer(k).unsign(...)` 解序列化器的 token 只会抛 `BadSignature`。

“绕过”的确切路径：`Serializer.__init__` 把非 `None` 的 salt 原样存进 `self.salt`（`serializer.py:212-216`，注释写明“if salt is None then the signer's default is used”）；`make_signer()` 在 salt 为 `None` 时取 `self.salt`，然后**始终以关键字形式**调用 `self.signer(self.secret_keys, salt=salt, **self.signer_kwargs)`（`serializer.py:280-287`）。默认 `Serializer(k)` 的 `self.salt` 是 `b'itsdangerous'`，于是造出来的 signer 构造里永远带一个显式 `salt=b'itsdangerous'`，`Signer.__init__` 形参上的默认值 `b'itsdangerous.Signer'`（`signer.py:132`）根本没有机会生效。实测 `Serializer(k).make_signer().salt == b'itsdangerous'`。签名层那份默认值只有两条路能见到：直接用 `Signer(k)`，或者序列化器显式 `Serializer(k, salt=None)`——此时 `self.salt is None`，`make_signer` 把 `None` 透传进去，`signer.py:155-156` 才把它补回 `b'itsdangerous.Signer'`（实测该路径 `make_signer().salt == b'itsdangerous.Signer'`）。

## C 段：同一条过期 token，两个入口相反的承诺

冻结在 `2020-01-01 00:00:00 UTC` 用 `TimedSerializer(k)` 签发，token 为：

```
{"u": 42}.XgvhAA.4AY4OgdD4RMnkrCCo-YcFZVNnO0
```

中间段 `XgvhAA` 解出整数 `1577836800`，即 `2020-01-01 00:00:00+00:00`。在 `2020-01-01 00:10:00 UTC`（年龄 600 秒）以 `max_age=60` 走两个入口，读数相反：

- **`loads(ttok, max_age=60)` 抛异常**：`SignatureExpired: Signature age 600 > 60 seconds`。判据在 `TimestampSigner.unsign`（`timed.py:138-153`）：先让 `Signer.unsign` 验签名本身（通过），再算 `age = self.get_timestamp() - ts_int`，`age > max_age` 就抛 `SignatureExpired`，同时把明文载荷挂在异常上（`payload=b'{"u": 42}'`、`date_signed=2020-01-01 00:00:00+00:00`）；`age < 0`（未来时间戳）同样抛。`TimedSerializer.loads` 捕获到 `SignatureExpired` 后立即重抛、不试 fallback（`timed.py:213-216`）。不传 `max_age` 时年龄判据整段跳过，所以脚本第三行 `loads(ttok)` 直接返回 `{'u': 42}`——时间戳默认只是记录，不构成有效期。
- **`loads_unsafe(ttok, max_age=60)` 不抛，返回 `(False, {'u': 42})`**：布尔位为假，载荷照样 JSON 解出来。判据在 `Serializer._loads_unsafe_impl`（`serializer.py:382-397`）：先正常调一次 `self.loads(s, salt, max_age=...)`（`TimedSerializer.loads_unsafe` 在 `timed.py:222-228` 把 `max_age` 透传进去）；捕获到 `BadSignature`（`SignatureExpired` 是其子类，见 `exc.py` 中 `BadTimeSignature`/`SignatureExpired` 继承链）后，只要异常上的 `payload` 不为 `None`，就调 `load_payload` 强行解码并返回 `(False, payload)`，载荷本身解不动才回 `(False, None)`。过期异常恰好带 payload，于是走成 `(False, {'u': 42})`。

哪个承诺更该保住：**`loads` 这一侧的“过期即拒绝”**。`loads` 是安全默认入口，docstring 写明“`max_age` provided … In case the signature is outdated, `SignatureExpired` is raised”（`timed.py:192-197`）；`docs/timed.rst` 也把 `s.unsign(string, max_age=5) → itsdangerous.exc.SignatureExpired: Signature age 15 > 5 seconds` 作为主契约演示。另一侧 `loads_unsafe` 文档现在写的两句是：`Serializer.loads_unsafe` 的 docstring “Like `loads` but without verifying the signature … **This function never fails**. Use it for **debugging only** …”（`serializer.py:354-363`），以及 `docs/serializer.rst` 的 “`sig_okay, payload = s.loads_unsafe(data)` … The first item in the returned tuple is a boolean that indicates if the signature was correct.” 也就是说“不抛、用布尔位交代签名状态、仅供调试”本身就是它的文档承诺；在 `TimedSerializer` 上过期表现成 `(False, payload)` 是这套承诺的自然延伸，只是 bool 位把“签名有效但过期”和“签名伪造”压成了同一个 `False`，并不区分。

## 收口：能不能收成一套，收在哪边，谁付代价

两处不一致都是历史分层（`Signer` 早于 `Serializer`，`loads_unsafe` 在 0.15 加入）留下的，技术上都能收，但代价不对称。

**默认 salt 收成一个字面量**，两个方向当前测试都能全绿（用临时 `sitecustomize` 垫片各跑一遍，`297 passed` 都在；仓库里唯一硬编码签名的 `test_digests`（`tests/test_itsdangerous/test_serializer.py:193`）用的是显式 `salt="dev salt"`，不钉默认值）：

- 收在签名层：把 `Serializer` 默认改成 `b'itsdangerous.Signer'`。代价付给序列化器用户——所有现存的默认 `Serializer`/`URLSafeSerializer`/`TimedSerializer`/`URLSafeTimedSerializer` token 全部失效，且 `docs/concepts.rst`、`docs/serializer.rst` 里大量隐式默认示例的现场签名对不上。
- 收在序列化层：把 `Signer` 默认改成 `b'itsdangerous'`。代价付给裸 signer 用户——现存 `Signer`/`TimestampSigner` token 失效；顺带消失的还有两层默认 salt 天然不同带来的上下文隔离（`docs/concepts.rst:59-84` 承诺的“同密钥不同 salt 跨场景不可互换”，在两层都用默认值时不再免费成立）。

**过期契约收成一个行为** 则有真实测试成本：把 `TimedSerializer.loads_unsafe` 改成过期即抛，`297` 立刻变成 `293 passed, 4 failed`——`TestTimedSerializer.test_loads_unsafe`（`tests/test_itsdangerous/test_timed.py:96`，继承自 `tests/test_itsdangerous/test_serializer.py:90` 的 `test_loads_unsafe`）以及 URLSafe 侧的三个参数化变体；反过来让 `loads` 过期也吐 payload，则直接违背 `test_max_age`（`tests/test_itsdangerous/test_timed.py:101-111`、`34-43`）和文档主示例。

**选择：两处都不收，保留现状。** salt 这边选序列化层现状（`b'itsdangerous'` 给序列化器，`b'itsdangerous.Signer'` 留给裸 signer），因为 `docs/concepts.rst` 明说“Typically, you'll want to use a serializer, not a signer”，序列化器是主入口、token 存量更大，动它付代价的人最多；要消除混淆，成本最低的是文档点名（B 段那条“signer 必须显式带 `salt=b'itsdangerous'`”就是应当写进文档的事实），而不是改字面量。过期这边选保住 `loads` 的抛异常契约，`loads_unsafe` 的 `(False, payload)` 维持“never fails / debugging only”，付代价的只是想拿布尔位区分“过期”和“伪造”的调用方——他们应当改用捕获 `SignatureExpired` 的主入口，而不是让 unsafe 入口开始抛异常。
