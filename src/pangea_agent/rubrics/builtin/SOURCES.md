# 专项方法论来源基线

本文件记录内置专项方法论的权威规范与开源实现基线。方法论只提炼分析维度，不把参考项目的默认值、
错误码、设备能力或恢复行为当成目标仓库事实。GitHub 引用固定到 2026-08-29 查询到的提交；更新引用
需要重新审核受影响的方法论。

## 协议规范

- NVMe：NVM Express Base Specification 2.4、NVM Command Set Specification 1.3，以及对应 PCIe、
  RDMA、TCP Transport Specification；来源：https://nvmexpress.org/specifications/ 。
- SAS：SAS-4.1，INCITS 567-2023；来源：https://www.t10.org/members/w_sas41-.htm 。
- SCSI：SPC-6，INCITS 566-2025；SBC-5，INCITS 571-2025；来源：https://t10.org/drafts.htm 。

## GitHub 固定引用

| 用途 | 仓库 | 分支 | 固定提交 |
| --- | --- | --- | --- |
| NVMe 类型、命令构造、发现与管理 | `linux-nvme/libnvme` | `master` | `ad61ac8a319ad0823c1c9861eecbf66125f8b9a1` |
| NVMe 命令、用户观测与测试 | `linux-nvme/nvme-cli` | `master` | `c8ec7e41f3738b20828849228a549f4ed1d03fc0` |
| NVMe/NVMe-oF、iSCSI 与用户态资源生命周期 | `spdk/spdk` | `master` | `f0b63ed866f0e4d3c56e73bd4a98d0042956147a` |
| NVMe 与块层回归模式 | `linux-blktests/blktests` | `master` | `4b02f0816ad7253177df4e99582e8213a5cdbfdb` |
| SAS transport、libsas、SCSI EH 与 scsi_debug | `torvalds/linux` | `master` | `cf72cbb39da84b6f02f90c07f33b102fc10b16f0` |
| SCSI 命令、sense、SES 与 SAS 观测 | `doug-gilbert/sg3_utils` | `main` | `b356cfc7af95710046c7fe0b2cbcb91b6d0e9c57` |
| SCSI/SAS/NVMe 健康、日志与自检 | `smartmontools/smartmontools` | `main` | `618fcaede4478bc7d17fa2a8db5fd18af3744e20` |
| iSCSI initiator、session 与恢复参考 | `open-iscsi/open-iscsi` | `master` | `8112cdd9514df076dc64ca3d4e85283aa701ce7e` |
| 通用 verbs、RDMA CM 与 mlx provider | `linux-rdma/rdma-core` | `master` | `1229eaa0471de7e3fbbe144b5839e301ead05334` |
| DPDK EAL、mempool、mbuf、ring 与 PMD 自测 | `DPDK/dpdk` | `main` | `d55ccd4e6de64e3f797f60de9e81f1d60f849775` |
| NVIDIA DOCA 官方 samples | `NVIDIA-DOCA/doca-samples` | `3.4.0` | `9c7dc8fce1ca3a592e333242e5a6a86b08fef4a0` |

## 使用边界

- 规范版本先决定字段、状态与能力的解释范围；参考实现只补充可观察的生命周期和测试形状。
- 方法论引用不得成为当前 RiskCard 的直接证据。风险仍须绑定当前冻结源码、需求、设计或运行结果。
- 目标声明旧版本、不同 transport、不同 provider 或不同设备能力时，以目标契约为准，并记录差异。
- 固定提交只用于可复核来源，不把上游测试脚本直接复制成 PANGEA TestCase。
