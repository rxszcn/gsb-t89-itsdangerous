# 两层默认 salt 与过期契约的现状读数

复现:`PYTHONPATH=src .venv/bin/python repro/default-salt.py`(A/B/C 三段)。
基线:`PYTHONPATH=src .venv/bin/pytest -q` 两遍均为 `297 passed`,本文不改任何实现。

## 一、A 段:两个默认 salt 的字面量,以及 salt 有没有被忽略

- 签名层:`Signer.__init__` 的默认 salt 是 `b"itsdangerous.Signer"`,共 19 字节
  (`src/itsdangerous/signer.py:132`;`salt=None` 时在 `src/itsdangerous/signer.py:156` 兜底回同一个字面量)。
- 序列化层:`Serializer.__init__` 的默认 salt 是 `b"itsdangerous"`,共 12 字节
  (`src/itsdangerous/serializer.py:195`,四个 overload 签名同值,如 `src/itsdangerous/serializer.py:113`)。
- A 段实测:`Signer(k).salt == b'itsdangerous.Signer'`,`Serializer(k).salt == b'itsdangerous'`。
- 默认 key 派生:`Signer.default_key_derivation == "django-concat"`(`src/itsdangerous/signer.py:127`),
  派生式为 `digest_method(self.salt + b"signer" + secret_key).digest()`(`src/itsdangerous/signer.py:202`-`204`)。
  salt 被直接拼进哈希输入,是派生密钥的一部分,所以这套派生"离不开"salt——不是可选调味,是密钥材料。
- 有没有被忽略:**没有**。A 段实测 `Signer(k, salt=b"totally-different").sign(b"x") == Signer(k).sign(b"x")`
  结果为 `False`——换了 salt 签名就变。三种内置派生(`concat`/`django-concat`/`hmac`)全部消费 salt,
  只有 `key_derivation="none"`(`src/itsdangerous/signer.py:208`)才真正不碰 salt。

## 二、B 段:同一个 token 拿两份默认 salt 去验

- 造 token:`Serializer(k, signer_kwargs={"key_derivation": "hmac"})`,产出
  `{"u": 1}.CcZd1RqEih7ZoQu-jziX66Hfjps`。
- 用签名层默认值 `Signer(k, key_derivation="hmac")`(salt = `b"itsdangerous.Signer"`)验:**False**。
- 用序列化层默认值 `Signer(k, key_derivation="hmac", salt=b"itsdangerous")` 验:**True**。
- 也就是说,裸 `Signer` 要验 serializer 出的 token,必须把 salt 改成 `b"itsdangerous"`(序列化层那份)。
- 两份默认值各自的位置:签名层 `b"itsdangerous.Signer"` 在 `src/itsdangerous/signer.py:132`;
  序列化层 `b"itsdangerous"` 在 `src/itsdangerous/serializer.py:195`。
- 绕过机制:`Serializer.make_signer`(`src/itsdangerous/serializer.py:280`-`287`)在 `salt is None` 时
  取 `self.salt`,然后**总是显式** `self.signer(self.secret_keys, salt=salt, **self.signer_kwargs)`。
  因为实参永远不是 `None`,`Signer.__init__` 里 `b"itsdangerous.Signer"` 那个默认值
  (`src/itsdangerous/signer.py:132` 与 `:156` 的兜底)永远轮不到生效——序列化层用自己的
  `b"itsdangerous"` 把签名层的默认值整个顶掉了。两层"默认 salt"因此从不相遇,只在有人跨层
  裸造 `Signer` 去验 serializer 的 token 时才撞车。

## 三、C 段:一条过期 token 走两个入口

- token:`TimedSerializer(k)` 在 2020-01-01 00:00:00 签出
  `{"u": 42}.XgvhAA.4AY4OgdD4RMnkrCCo-YcFZVNnO0`,10 分钟后以 `max_age=60` 读。
- 入口一 `loads`:抛 `SignatureExpired: Signature age 600 > 60 seconds`。
  判据在 `src/itsdangerous/timed.py:139`-`148`:`age = get_timestamp() - ts_int`,`age > max_age`
  即抛(600 > 60);`TimedSerializer.loads` 对 `SignatureExpired` 直接 `raise`、不再尝试后续
  signer(`src/itsdangerous/timed.py:213`-`215`)。
- 入口二 `loads_unsafe`:返回 `(False, {'u': 42})`——载荷照样解出来,只回一个假。
  判据在 `src/itsdangerous/serializer.py:368`-`394` 的 `_loads_unsafe_impl`:先 `self.loads(...)`,
  捕获 `BadSignature`(`SignatureExpired` 经 `BadTimeSignature` 继承自 `BadSignature`,
  `src/itsdangerous/exc.py:31`);只要异常带着 `payload`(`SignatureExpired` 在
  `src/itsdangerous/timed.py:143`-`147` 塞了 `payload=value`),就 `load_payload(e.payload)`
  把载荷解出来,连同 `False` 一起返回。过期与篡改在这里被压成同一个布尔 `False`。
- 对照组:不带 `max_age` 的 `loads` 正常返回 `{'u': 42}`,说明签名本身没坏,只是超龄。
- 更该保住的承诺:`loads` 的"超龄即拒绝"。`max_age` 是调用方显式设的安全门槛,而
  `loads_unsafe` 把过期载荷原样交出,等于给"只看 payload 不看布尔"的调用方留了一条绕过
  过期语义的路。文档现在写的那一句是 `loads_unsafe` docstring 里的 "This function never
  fails."(`src/itsdangerous/serializer.py:359`)——字面上它没违约(确实没抛),但
  "never fails" 的代价是把过期这件事降级成一个可以被忽略的布尔;`docs/serializer.rst:73`
  只说 "The first item in the returned tuple is a boolean that indicates if the
  signature was correct.",对过期载荷会被一并交出只字未提。

## 四、收口:能不能收成一套,收在哪层,谁付代价

- 两份默认 salt 字面量**可以**统一,但只能二选一,且都要有人付存量 token 的代价:
  - 收在签名层(把 `Signer` 默认改成 `b"itsdangerous"`):所有用裸 `Signer` 默认值签过的
    存量签名全部失效;`src/itsdangerous/signer.py:132` 与 `:156` 两处都要动。
  - 收在序列化层(把 `Serializer` 默认改成 `b"itsdangerous.Signer"`,或让 `make_signer`
    传 `None` 放行签名层默认):所有现存 serializer token(含 `TimedSerializer`、
    `URLSafeSerializer` 及下游 Flask session 一类)全部验不过,波及面更大。
- 现有测试里直接钉住这套行为的是 `tests/test_itsdangerous/test_serializer.py:123`
  (`test_alt_salt`,显式 salt 的往返)和 `tests/test_itsdangerous/test_serializer.py:188`
  (`salt="dev salt"` 的 factory);字面量本身没有被断言,但任何一边改默认值都会改变
  派生密钥,凡涉及固定签名读数的用例都会跟着翻。
- 过期语义可以收在 `src/itsdangerous/serializer.py:368` 的 `_loads_unsafe_impl`:对
  `SignatureExpired` 单独处理(比如不交出 payload,或把过期与篡改区分开)。现状测试没有
  覆盖"过期走 `loads_unsafe`"这条路径(`tests/test_itsdangerous/test_timed.py` 只测
  `loads`/`unsign` 的 `max_age`),所以这条收紧不碰现有 297 个用例,但会实质修改
  "never fails / 总能拿到 payload" 的隐含承诺。
- 我的选择:**salt 不合并字面量,只合并规则**——把"Serializer 永远显式给 signer 一份
  salt,`Signer` 的默认值只对裸用生效"写成明文契约,代价由文档和跨层裸造 `Signer` 的
  调用方付(他们必须知道用 `b"itsdangerous"`);过期那边则收在序列化层的
  `_loads_unsafe_impl`,让 `loads_unsafe` 对过期不再交出载荷,代价由依赖
  `(False, payload)` 读过期内容的调用方付。理由:`loads` 的"超龄即拒绝"是安全语义,
  值得保;`loads_unsafe` 的 "never fails" 是便利语义,且当前文档并未承诺过期也给载荷,
  收紧它不伤明文契约。
