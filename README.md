# Word2JATS · Word 稿件的智能结构化转换

JiangLab 参赛作品，学术期刊结构化技术创新大赛选题一。

将学术论文的 Word 稿件（.docx 格式）转换为 JATS 1.3 XML 与配套图片，并提供内容预览、原稿对照及文章信息修改。在线入口：[word2jats.jianglab.work](https://word2jats.jianglab.work)。

当前分支包含提交后的 Web 改进，尚未替换上述在线版本。独立预览、改进范围与验收记录见 [Web 后续改进](docs/11-重构工程/Web后续改进-20260915.md)。已提交材料保持不变。

## 方法与使用价值

大语言模型负责理解结构，程序按原稿位置取回正文与文献内容；文首作者、机构等采用受检查约束的局部 JATS 通道。自动检查结合原稿覆盖、输出来源、DTD、媒体及引用关系。用户可在浏览器中对照原稿，修改题名、作者、单位、通讯信息、ORCID 和出版信息，再导出更新后的 XML。

人工修改保存到独立版本，预览、相应检查与下载文件同步更新，不需要重新调用模型。原自动结果保留，可随时恢复。

## 质量、效率与模型用量

实验比较 Qwen 与 DeepSeek 两种配置，覆盖文首字段、作者关系、摘要关键词、章节、图表、数学公式、文献字段和交叉引用，并记录每篇转换时间及模型响应返回的输入、输出、缓存命中和未命中 Token。

实验设置与结果见 [评测与验证](docs/06-评测与成绩.md)，完整方法见技术方案说明书。结果页可查看生成分析结果的模型用量，并区分本次新增与复用的原始计量；相同记录随转换成果下载。

## 快速运行

需要 Python 3.10+；首次转换需要云模型 API 凭据，本机不需要 GPU。Qwen 配置的主流程为 qwen3.7-plus、文首为 qwen3.8-max；DeepSeek 配置为 deepseek-flash。

    python -m venv .venv
    .venv/bin/python -m pip install -r requirements.txt
    cp .env.example .env

在本地编辑 .env：使用 Qwen 填写 DASHSCOPE_API_KEY，使用 DeepSeek 填写 DEEPSEEK_API_KEY。然后启动网页：

    .venv/bin/python -m webapp

访问 http://127.0.0.1:8000，选择 DeepSeek 或 Qwen，上传 Word。转换后可通过分层目录浏览结果、对照原稿、修改信息，再下载当前保存版本的 XML 和配套图片。首页保留此浏览器的近期任务入口。需要重新识别时，在结果页的“更多”中选择“重新转换”；新任务不会覆盖当前结果。

命令行转换示例：

    PYTHONPATH=src .venv/bin/python -m word2jats convert \
      样例数据/03/初始文件.docx \
      --journal JIN --doi 10.31083/JIN49347 --llm deepseek -o output/

测试：

    .venv/bin/python -m pip install -r requirements-dev.txt
    .venv/bin/python -m pytest -q

完整操作见 [安装与使用](docs/09-安装与使用.md)。

## 已提交材料

决赛材料已经提交，包含技术方案说明书 PDF 和可运行原型，按队名 JiangLab 组织。提交原型带有专供评审使用的配置，不应公开传播；实验稿件、历史输出、缓存与内部开发文档未进入原型。具体冻结版本见 [提交记录](docs/11-重构工程/最后检查与压缩提交-20260914.md)。后续 Web 改进在独立分支进行，不覆盖已提交材料。

开发仓库中的输出和缓存位于 reports/，不是转换器的输入依赖。

## 文档与范围

设计见 [系统设计](docs/04-系统设计.md)，初赛路线与决赛版的变化见 [技术方案对照](docs/初赛与决赛技术方案对照.md)，编辑流程见 [Web 应用](docs/08-Web应用.md)。

复杂压平表格、旧式嵌入对象和部分引文可结合原稿进一步处理。网页提供受控的文章信息编辑，不提供整篇自由编辑或多人审签。部署时按实际业务管理访问、配额及稿件留存。

源码许可见 [LICENSE](LICENSE)。JATS DTD、OMML2MML.XSL、NLM/NCBI 预览样式表等组件的许可说明随资源保留。
