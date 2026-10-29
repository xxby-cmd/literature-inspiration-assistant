# 第三方组件

本项目不把第三方二进制文件、账号文件或内部站点插件打包进源码。安装时从官方分发取得依赖，保留各包自己的许可证。

主要组件：

- [Python](https://www.python.org/)：Python Software Foundation License。
- [Node.js](https://github.com/nodejs/node)：MIT及其随附第三方声明。
- [React](https://github.com/facebook/react)、[Next.js](https://github.com/vercel/next.js)、[Vinext](https://github.com/cloudflare/vinext)、[Vite](https://github.com/vitejs/vite)：MIT。
- [Cloudflare Workers SDK / Wrangler](https://github.com/cloudflare/workers-sdk)：MIT或Apache-2.0，以具体包许可证为准。
- [cloudflared](https://github.com/cloudflare/cloudflared)：Apache-2.0，安装时另行下载。
- [Codex](https://github.com/openai/codex)：官方项目许可证与服务条款分别适用；本项目不分发其登录信息或模型服务。
- [unified](https://github.com/unifiedjs/unified)、remark/rehype、[KaTeX](https://github.com/KaTeX/KaTeX)：MIT及各包声明。

完整前端版本和下载完整性见`reader/package-lock.json`；依赖自身的`LICENSE`和`NOTICE`仍是准确依据。论文著作权与软件MIT许可无关，不随本项目开源而开放全文转载或翻译权限。
