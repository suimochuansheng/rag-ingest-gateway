---
title: 微服务架构设计规范
author: 架构组
date: 2026-07-01
---

# 微服务架构设计规范

本文档规定了公司内部微服务架构的设计标准，适用于所有新建和重构的服务。

## 1. 服务拆分原则

### 1.1 单一职责原则
每个微服务应只负责一个独立的业务领域。例如，`用户服务`只处理用户身份认证和基础信息管理，`订单服务`只处理订单生命周期。

### 1.2 数据库隔离
每个微服务应拥有独立的数据库实例或 Schema。禁止多个服务共享同一张表。

| 服务名称 | 数据库名 | 负责人 |
| :--- | :--- | :--- |
| 用户服务 | `user_db` | 张三 |
| 订单服务 | `order_db` | 李四 |
| 支付服务 | `payment_db` | 王五 |

## 2. 通信协议

### 2.1 同步通信
- 使用 RESTful API，格式为 JSON
- 超时时间设置为 3 秒，重试次数不超过 2 次

### 2.2 异步通信
- 使用 RabbitMQ 作为消息中间件
- 消息体大小不超过 1MB

> 注：对于最终一致性场景，推荐使用异步通信模式。

## 3. 服务注册与发现

服务启动后应向 Consul 注册自身信息，并定期发送心跳（间隔 10 秒）。连续 3 次心跳失败后，服务将被标记为不健康。

```python
# 服务注册示例
def register_service(service_name, port):
    consul.agent.service.register(
        name=service_name,
        port=port,
        check=consul.Check.http(f"http://localhost:{port}/health", interval="10s")
    )
```
## 4. 配置管理
所有环境配置统一存放在 Apollo 配置中心，按 dev / test / prod 环境隔离。