📘 rclone install & config (official + practical)

1️⃣ Install rclone (macOS)
brew install rclone


Verify:

rclone version

2️⃣ Where rclone config lives
rclone config file


Default on macOS:

~/.config/rclone/rclone.conf


This file stores all remotes (Azure, Alibaba OSS, etc.).

3️⃣ Create a new remote (interactive)
rclone config


You’ll see:

n) New remote
s) Set configuration password
q) Quit config

Create a remote
n
name> my-remote-name


Then select the storage type (Azure, S3, etc.).

4️⃣ Example configs (most relevant to you)
🔹 Azure Blob (using SAS – recommended)
Storage> azureblob
account> <storage-account-name>
key> (leave empty)
sas_url> https://<account>.blob.core.windows.net/<container>?sv=...


Test:

rclone lsd azure-sbcrecordingsa:

🔹 Alibaba Cloud OSS (S3-compatible)
Storage> s3
provider> Alibaba
access_key_id> LTAIxxxxxxxx
secret_access_key> ********
region> me-central-1
endpoint> oss-me-central-1.aliyuncs.com


Test:

rclone lsd alibaba-oss-masdr-data-prod:

5️⃣ List all configured remotes
rclone listremotes


Example:

azure-sbcrecordingsa:
alibaba-oss-masdr-data-prod:

6️⃣ Non-interactive (scripted) config (very useful)
rclone config create alibaba-oss-masdr-data-prod s3 \
  provider=Alibaba \
  access_key_id=YOUR_AK \
  secret_access_key=YOUR_SK \
  region=me-central-1 \
  endpoint=oss-me-central-1.aliyuncs.com

7️⃣ Encrypt rclone config (recommended)
rclone config


Choose:

s) Set configuration password


This encrypts AK/SAS tokens at rest.

8️⃣ Debugging config issues
rclone listremotes
rclone config show
rclone lsd REMOTE: -vv


These commands are safe (secrets are masked).
