# HP ローカル編集

非エンジニアが、ブラウザー右下のチャットでホームページを修正するためのCodexプラグインです。
現在の配布候補は **0.3.13 / macOS Apple Silicon** です。

## 編集から公開まで

1. Codexで「HP編集を始める」と伝えると、ローカルの確認ページがブラウザーで開きます。
2. 右下のチャットで修正を依頼し、手元のページを確認します。
3. **確認用に反映** を押し、完了後に **確認用サイトを開く** で確認します。
4. **公開する** を押し、版番号と変更概要を確認して、確認画面の公開ボタンで確定します。

Enterで送信、Shift + Enterで改行します。処理状況は自動更新されます。
「確認用 v2 / 公開中 v1」で内容の版を区別し、公開時は確認した版番号を引き継ぎます。
変更概要には文章の変更前後や追加・削除が表示され、公開済みの画面では結果を確認できます。

## 導入案内

配布用ZIPには、ネイティブ実行環境、HP接続設定、編集スキル、利用者向けガイドを同梱します。
利用者にはCodexを使えるデスクトップアプリと、運用担当に登録したGoogleアカウントが必要です。
サイトへの権限は運用担当が事前に設定します。

利用者のGitHub・SIMY登録、Git・Python・別のCodex CLI・Driveプラグインの追加導入は不要です。
対応済みの配布対象はMacのApple Siliconです。Windows・Intel Macの受入検証は未実施です。

ZIPの導入から、編集・公開・再開までをまとめています。[利用者向けガイド](plugins/hp-local-editor/skills/hp-local-editor/references/customer-guide.md)

## 配布・更新

配布候補は `tools/build_desktop_plugin.py` で作成します。マーケットプレイス定義を含むZIPを
利用者に渡し、実行ファイルのハッシュと同梱ソースのハッシュをビルド記録で確認します。
GitHubのソースアーカイブにはネイティブ実行環境が含まれません。

ローカルソースへの登録後、アプリのプラグイン画面で導入とGoogle接続を確認します。
更新時は作業領域を保ち、登録先のプラグインを更新してアプリを再起動します。
実際のアプリ・アカウントで利用できる導入方法を確認してください。
[公式のローカル導入方法](https://developers.openai.com/plugins/build/plugins#install-a-local-plugin-manually)

詳細なビルド・配布物の違い・検証手順を記載しています。[運用担当向けビルド手順](tools/README.md)

## 実装と接続

- [編集スキル](plugins/hp-local-editor/skills/hp-local-editor/SKILL.md)
- [ローカル確認・チャット・復旧](plugins/hp-local-editor/skills/hp-local-editor/references/local-preview.md)
- [保存・配信の契約](plugins/hp-local-editor/skills/hp-local-editor/references/protocol.md)
- [プラグイン定義](plugins/hp-local-editor/plugin.json)
- [HP接続定義](plugins/hp-local-editor/mcp.json)

サイトの原本と履歴は運用側GitHub、静的サイトの配信はAWSで管理します。
編集は利用者のMac上のCodexで行い、保存・検証・配信はHPサービスが処理します。
作業領域の `.hp/` は原本、保存記録、会話、途中処理の復旧に使います。

## 検証

```sh
python3 -m unittest discover -s tests -v
node --test tests/test_preview_ui.cjs
```

単体テスト、ネイティブ配布物の動作、実サービスとの接続、画面の目視、
新規アカウントでの導入は、それぞれの実施結果をリリース記録に残します。
公式ディレクトリへの掲載状況は、ローカルの配布候補作成とあわせて運用担当が確認します。
