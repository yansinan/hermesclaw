提交说明与操作步骤

背景
- 本分支已在容器内 commit 为 feature/container-hermes-side。
- 已在容器路径生成 git bundle：/opt/data/hermesclaw/feature-container-hermes-side.bundle
- 远端 push 因容器内缺少 Git 凭证失败（错误：could not read Username for 'https://github.com'）。

在有凭证的宿主机或发布机上执行（推荐）

1) 取得 bundle 到宿主机（如果宿主机无法直接访问容器路径）：
   - 方式 A：容器可直接共享 /opt/data，直接使用该路径；
   - 方式 B：从容器拷贝到宿主机（替换 <container>）：
     docker cp <container>:/opt/data/hermesclaw/feature-container-hermes-side.bundle .

2) 克隆官方仓库或使用现有克隆：
   git clone https://github.com/AaronWong1999/hermesclaw.git /tmp/hermesclaw
   cd /tmp/hermesclaw

3) 从 bundle 导入分支到本地仓库（假设 bundle 在 /opt/data/hermesclaw/feature-container-hermes-side.bundle 或当前目录）：
   # 如果 bundle 位于宿主机的同一路径：
   git fetch /opt/data/hermesclaw/feature-container-hermes-side.bundle feature/container-hermes-side:feature/container-hermes-side

   # 或者从当前目录的 bundle：
   git fetch ../feature-container-hermes-side.bundle feature/container-hermes-side:feature/container-hermes-side

4) 切换到该分支并检查提交：
   git checkout feature/container-hermes-side
   git log --oneline -n 5

5) 推送到远端并创建 PR：
   git push origin feature/container-hermes-side

   # 用 gh 创建 PR（可选）：
   gh pr create --base main --head feature/container-hermes-side --title "feat: per-message @mention routing and tagging" --body "支持 per-message @mention 路由、动态别名、始终加前缀并修复解包错误。"

6) 验证（远端检查）：
   git remote -v
   git branch -r | grep feature/container-hermes-side || true

常见问题
- push 报错 403/权限：说明当前机器没有写权限，需要配置用户名/密码或 PAT（环境变量或 git credential helper）。
- 若宿主机无法直接访问容器内 /opt/data 路径，请先用 docker cp 把 bundle 拷出。

我已把 bundle 保存在：/opt/data/hermesclaw/feature-container-hermes-side.bundle
我也已把此次提交信息写入本地 commit（branch: feature/container-hermes-side）。

如果你要我代为 push（在我有凭证时），告诉我如何安全提供凭证或把凭证放到容器环境变量中；否则请在宿主机按上面步骤操作。