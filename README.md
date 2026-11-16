# amz-filament-feed

Amazon.co.jp のフィラメント（PLA / PETG / TPU / TPE）のセール品を mcp-server 上のスクリプトが定期収集し、RSS として配信する疑似フィード。

- feed.xml: 生成された RSS（フィードリーダーは raw URL を購読）
- generate.py: 収集・生成スクリプト（状態は ~/.local/state/amz-filament-feed/state.json）
- run.sh: 生成して、変わっていたら commit + push
- 実行: systemd user timer amz-filament-feed.timer（1日2回）
