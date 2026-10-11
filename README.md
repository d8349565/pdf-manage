# 纸序 PDF

**免费、开源、离线的 Windows PDF 工具箱。** 阅读、整理、打印、压缩加密、格式转换，一个窗口搞定。

文件只在你的电脑上处理，不联网、不上传、不用登录。

[![下载](https://img.shields.io/badge/下载-Windows%20安装包-0078d4?labelColor=444c56)](https://github.com/d8349565/pdf-manage/releases/latest)
[![License MIT](https://img.shields.io/badge/License-MIT-0078d4?labelColor=444c56)](LICENSE)
[![隐私 不联网不上传](https://img.shields.io/badge/隐私-不联网不上传-2ea043?labelColor=444c56)](#使用前请注意)

[更新记录](CHANGELOG.md)

![阅读与页面整理](docs/images/reading.png)

![拖动排序整理页面](docs/images/arrange.png)

## 快速开始

1. 到 [Releases](https://github.com/d8349565/pdf-manage/releases/latest) 下载并运行 `ZhiXuPDF-Setup.exe`。
2. 把 PDF 拖进窗口。
3. 在“阅读”里看，在“整理”里改，完成后点“保存”；想保留原文件就点“另存为”。

## 功能

| 场景 | 能做什么 |
| --- | --- |
| 阅读与目录 | 连续翻页、缩放、跳转；侧栏大纲书签与层级编辑 |
| 审阅与修正 | 高亮、下划线、便签、画笔、图形批注；单行文字修正与撤销 |
| 整理 | 合并、拆分、提取页面；拖动排序、旋转、删除；支持撤销 |
| 打印 | 先预览，再选页码、纸张和份数 |
| 压缩与加密 | 缩小体积；打开密码、免密阅读权限保护或双重保护 |
| 页码 | 添加可见页码、跳过封面、奇偶页外侧；阅读器页标签与批量输出 |
| 转换 | 图片与 PDF 互转；PDF 表格转 Excel；Word 与 PDF 批量互转；Markdown 转 PDF |

## 使用前请注意

- **表格转 Excel**：仅适用于带文字层的电子 PDF。扫描件不支持 OCR，转换结果请人工核对。
- **Word 与 PDF 互转**：需要电脑已安装 Microsoft Word；PDF 转 Word 需要 Word 2013 或更高版本。首次使用会明确询问是否关闭 Word 的重复 PDF 导入确认。复杂版式和图片型页面需核对，图片不保证可编辑；加密文件需先另存解密副本。
- **Markdown 转 PDF**：使用 UTF-8 文件，支持常规 Markdown 和中文，仅加载同目录及子目录的本地图片。公式和 Mermaid 按源码显示。
- **文字修正**：仅支持单行横排可见文字，预览后应用；会实际移除旧字。选区与图片、表单、链接或隐藏文字重叠时拒绝修改，较长文字请转 Word 编辑。
- **权限保护**：免密阅读的打印和复制限制依赖阅读器遵守，不能保证绝对防复制。
- **暂不支持**：OCR、完整段落编辑、正文目录生成、全文搜索。
- **请及时保存**：页面修改可撤销，但没有自动恢复草稿。带签名的文件会以只读方式保护。

## 反馈与交流

如果在使用中遇到问题或有改进建议，欢迎提交 [GitHub Issues](https://github.com/d8349565/pdf-manage/issues)。如果觉得好用，欢迎点亮 ⭐ **Star** 支持一下！

## 开源协议

本项目基于 [MIT 许可证](LICENSE) 开源，可免费用于个人及商业用途，支持自由修改与二次分发。
