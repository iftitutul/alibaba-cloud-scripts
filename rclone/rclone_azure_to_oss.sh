# rclone copy "azure-sbcrecordingsa:migrationtest/" \                                                                                                                    ok | % | at 14:40:10 
#             "alibaba-oss-masdr-data-prod:migration-test-tbd/" \              
#   --progress --stats=30s --stats-one-line \
#   --log-level=INFO --log-file=rclone_azure_to_oss_manyfiles.log \
#   --retries=12 --retries-sleep=10s --low-level-retries=50 \
#   --checkers=32 --transfers=12 \
#   --fast-list \
#   --s3-chunk-size=256M --s3-upload-concurrency=4 \
#   --tpslimit=10 --tpslimit-burst=20

#!/usr/bin/env bash
set -euo pipefail

# ----------------------------
# Variables
# ----------------------------
SRC="azure-sbcrecordingsa:migrationtest/"
DST="alibaba-oss-masdr-data-prod:migration-test-tbd/"

DATE_STR=$(date +"%Y%m%d_%H%M%S")
LOG_FILE="rclone_azure_to_oss_${DATE_STR}.log"

START_TIME=$(date +"%Y-%m-%d %H:%M:%S")

# ----------------------------
# Log header
# ----------------------------
{
  echo "======================================================"
  echo "RCLONE COPY STARTED"
  echo "Start Time : ${START_TIME}"
  echo "Source     : ${SRC}"
  echo "Destination: ${DST}"
  echo "Log File   : ${LOG_FILE}"
  echo "======================================================"
} | tee -a "${LOG_FILE}"

# ----------------------------
# Rclone execution
# ----------------------------
rclone copy "${SRC}" "${DST}" \
  --progress \
  --stats=30s \
  --stats-one-line \
  --log-level=INFO \
  --log-file="${LOG_FILE}" \
  --retries=12 \
  --retries-sleep=10s \
  --low-level-retries=50 \
  --checkers=32 \
  --transfers=12 \
  --fast-list \
  --s3-chunk-size=128M \
  --s3-upload-concurrency=3 \
  --tpslimit=10 \
  --tpslimit-burst=20

# ----------------------------
# Log footer
# ----------------------------
END_TIME=$(date +"%Y-%m-%d %H:%M:%S")

{
  echo "======================================================"
  echo "RCLONE COPY FINISHED"
  echo "End Time   : ${END_TIME}"
  echo "======================================================"
} | tee -a "${LOG_FILE}"
