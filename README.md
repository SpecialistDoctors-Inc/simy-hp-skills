# HP ローカル編集プラグイン（開発中）

Codexと一緒にホームページを通常のローカルファイルとして編集するためのパッケージです。
利用者のGit導入・GitHubアカウント・SIMYアカウントは不要です。
macOS / Linux の Python 3 標準ライブラリで動作します。v1 は Windows に対応していません。

接続先は試用環境として配備されています。GitなしのCodex CLI導入、Google本人確認、
実Driveとの受け渡し、下書き保存、確認用ページへの反映を検証しています。このパッケージの導入だけで
サイトへの権限や本番公開の許可は付与されません。利用者本人の接続と、実際に使う
Codexホストでの確認カードの検証は別に必要です。

## パッケージ

```text
.agents/plugins/marketplace.json
plugins/hp-local-editor/
  plugin.json
  mcp.json
  skills/hp-local-editor/
    SKILL.md
    references/protocol.md
    scripts/hp_local_editor.py
```

[ローカル編集スキル](plugins/hp-local-editor/skills/hp-local-editor/SKILL.md)と
[接続設定](plugins/hp-local-editor/mcp.json)を、独立したプラグインにまとめています。
インストール対象は `plugins/hp-local-editor` です。旧受付スキルはその外にあるため、
このプラグインには含まれません。公開パッケージにサイト本体、医院固有の設定、
Sheet・DriveのID、メールアドレス、認証情報は含めません。

## Gitを使わない導入手順

運用担当は、プラグインを含む検証済みの**40桁コミットSHA**と、そのZIPのSHA256を案内します。
`main` や可変ブランチを固定版の代わりに使わないでください。

1. Codexにログインし、運用担当の指定した固定ZIPをCodexにダウンロード・検証・展開してもらいます。
   GitのcloneやGit版マーケットプレイスの取得は使いません。
2. 展開したローカルフォルダーのマーケットプレイスを、利用者が確認したうえでローカルソースとして追加します。
   `.agents/plugins/marketplace.json` は `./plugins/hp-local-editor` だけを参照します。
3. 対応するCodexアプリのプラグイン画面で `HP ローカル編集` をインストールし、セットアップを実行します。
   ローカルソースの対応状況はホストによるため、実際の表示を確認します。
4. 利用者本人がホストの画面でGoogleログインと許可を行います。
   `hp_profile` と `hp_list_sites` で接続先・対象サイト・権限を確認します。
5. **Google Driveプラグインは別に接続します。** 編集元ZIPの取得と変更ZIPのアップロードに使い、
   Driveの実際の接続プロフィールを `hp_profile` の本人情報と照合します。変更ZIPの宛先は、
   認証済みの作業領域作成結果にある `upload_folder_id` だけを使います。貼り付けた別のIDや似た名前のフォルダーへは送信しません。
   HPサービスへのログインだけではDrive接続は完了しません。

固定ZIPのURL形式は
`https://codeload.github.com/SpecialistDoctors-Inc/simy-hp-skills/zip/<検証済み40桁コミットSHA>`
です。山括弧部分は実際に配布されたSHAへ置き換える必要があります。

Codexへの導入依頼例:

```text
運用担当が指定した検証済み40桁コミットSHAとZIPのSHA256を使い、
simy-hp-skillsの固定ZIPをダウンロードして、ハッシュを検証してください。
Gitは使わず、新しいローカルフォルダーに展開してください。
plugins/hp-local-editor/plugin.json と mcp.json、同梱スキルを確認して、
このプラグインだけをローカルソースから導入する準備をしてください。
個人設定や既存プラグインを上書きせず、実際のインストールはホストの画面で確認します。
今回は導入準備だけで、接続・依頼登録・サイト変更・保存・公開・メール送信は行いません。
```

ローカルソース登録に対応するCLIでは、利用者の確認後に
`codex plugin marketplace add /absolute/path/to/extracted-package` を使えます。
これは手順の説明であり、このリポジトリの検証では個人設定の変更や接続は行っていません。

## ローカル編集と保存

スナップショットを `workspace/site/` の普通のファイルとして開き、Codexが内容を編集します。
`.hp/` に原本・未確認の保存パッケージ・受領結果を保持します。
バックエンドAIによる変更生成や競合解決は使いません。

同梱のPythonヘルパーは通信やGit実行をせず、次を扱います。

- `init`: 認証済みの作業領域作成結果とZIPのハッシュ・サイト・作業領域・基準コミットを照合してから、新しい作業領域に展開。
- `status --diff`: 追加・変更・削除と、文章の差分を確認。
- `pack`: 明示的な削除と固定の操作IDを含む変更ZIPを作成。
- `accept`: 保存済み受領結果に一致する原本だけを更新し、ZIP作成後のローカル編集を保持。

認証済みの `hp_get_operation` で作業領域作成の完了を確認し、その `result`
（`kind: "workspace"`）を `open-result.json` として保存します。
その結果の `snapshot_file_id` からダウンロードしたZIPを、同梱スキルのディレクトリで
次のように検証・展開します。

```sh
python3 scripts/hp_local_editor.py init snapshot.zip workspace --receipt open-result.json
```

受領結果の `snapshot_sha256` とZIP全体、`site_id`・`workspace_id`・`base_commit` と
ZIPのマニフェストが一致しない場合、作業領域やロックファイルは作成しません。
ZIP内部のハッシュが整合するだけでは、正しいサイトから取得した証拠にはなりません。
失敗時も元のZIP・受領結果・既存の作業領域は保持します。
これは認証済みの結果とファイルを結びつける検証で、実際のGoogleログインを検証した証拠ではありません。

`pack` が返す `operation_id` を、そのまま `hp_save_draft.operation_id` に使います。
保存完了後の `hp_get_operation` の `result` が `kind: "saved"` の受領結果です。
`schema_version`、`site_id`、`workspace_id`、`operation_id`、`base_commit`、
`saved_commit`、`bundle_sha256` を含むこの結果だけを `accept` に渡します。
操作全体のラッパーや、まだ保存されていない受付結果は渡しません。

保存と公開は別の操作です。`hp_submit_preview` で確認ページを準備し、
`hp_prepare_publish` の確認カードから対象を確認します。`hp_confirm_publish` はアプリ専用です。
受付中・再確認中・通知の問題を、公開完了と同じ意味で表示しません。

処理が「要対応」で止まった場合、原因を解消した後に `hp_resume_operation` で
元の操作を再開できます。再開依頼にも固定のIDを付けるため、応答が失われても
同じ依頼を照合できます。取消済みの作業や、完了した公開のメールを送り直す操作ではありません。

## 接続形式と残る検証

`plugin.json` と `mcp.json` は Agent Plugins 1.0.0 の形式です。
接続先は `https://hp-control.simy.one/mcp` の Streamable HTTP です。
OAuthはサーバーの保護リソース・認可サーバーのメタデータから検出します。
サーバー側でDCR、同じリソースURL、`openid email hp:read hp:write` を扱い、
パッケージにクライアントシークレットや固定トークンを置きません。

公式OpenAIドキュメントにあるMCPごとの認証拡張は、現在のAgent Plugins 1.0.0の
公開MCPスキーマには定義されていません。そのため、このパッケージは有効な標準コアだけを宣言し、
認証はサーバー側の検出に任せます。プラグイン側の表示・セットアップ情報は、
対応する `extensions.com.openai` に置きます。

実際のホストでOAuth検出とDCR、Google本人確認、対象サイトの権限、Driveとのファイル受け渡し、
非公開メタデータとアプリ専用確認ボタンを確認する必要があります。
ホストによっては登録済みMCPの対応付けが後で必要になる場合があります。
このパッケージには未取得の登録IDを記載していません。配備・導入・本番公開の完了は主張しません。

形式の参照: [公式OpenAIプラグイン仕様](https://developers.openai.com/plugins/build/plugins)。

## 互換用の旧受付スキル

[hp-customer-intake](skills/hp-customer-intake/SKILL.md) と
[旧受付表仕様](skills/hp-customer-intake/references/workbook-contract.md)は変更せず残します。
こちらは会話を受付表へ記録する別の仕組みで、ローカル編集プラグインの導入対象には含めません。
旧スキルの既存固定リビジョンは
`0d41c574fe9c45f8ef1f2e638cf4fd39b9fa3fe5` です。

## 検証

`python3 -m unittest discover -s tests -v`

Pythonヘルパーは標準ライブラリのみを使用します。プラグインやスキルの配布自体は、
サービスの配備・実際のGoogleログイン・メールの受信・本番公開の検証とは別です。
