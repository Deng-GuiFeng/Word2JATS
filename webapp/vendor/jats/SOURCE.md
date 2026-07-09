# 来源与许可

`jats-html.xsl` 与 `jats-preview.css` 取自 **NCBI JATS Preview Stylesheets**：

- 仓库：https://github.com/ncbi/JATSPreviewStylesheets
- 主入口：`xslt/main/jats-html.xsl`（4000+ 行，自包含，无 import/include；XSLT 1.0，libxslt 可直接运行）
- 配套样式：仓库根 `jats-preview.css`

**许可：公有领域（美国国立医学图书馆 NLM 的政府作品）。** 样式表头注释原文：
> This work is in the public domain and may be reproduced, published or otherwise used
> without the permission of the National Library of Medicine (NLM). We request only that
> the NLM is cited as the source of the work.

据此在本项目中直接分发，署名来源为 **NLM / NCBI**。

本项目仅用它把 JATS XML 渲染成 HTML 预览；渲染前会改写图片 `xlink:href` 指向本服务的图片
接口，渲染后把 MathML 的 `mml:` 前缀去掉以便浏览器原生显示（见 `webapp/render.py`）。
