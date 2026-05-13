# AGENTS.md

## 1. 言語ポリシー（必須）
- すべての対話・出力は **日本語** で行うこと
- **Git のコミットメッセージは英語**で記述すること（Conventional Commits に従う）

## 2. ブラウザ操作（必須）
- fetch/curl が必要な場合は理由を説明してから実行すること

## 3. Git ワークフロー
### コミットメッセージのフォーマット
```
<type>: <description>

<optional body>
```

- 英語で記述し、Conventional Commits に従うこと
- 要約は約50文字、必要に応じて本文を追加する
- 種別: feat, fix, refactor, docs, test, chore, perf, ci

### コマンドトリガー
#### push を要求された場合（例: "push して", "プッシュして", "push this"）
1. 変更内容を確認する（`git status` / `git diff`）
2. ファイルをステージする（`git add`） — すでにステージ済みの場合はスキップ
3. 上記のコミットメッセージ形式に従ってコミットする
4. リモートへプッシュする（デフォルトブランチへの直接プッシュが不適切な場合は PR 作成を提案する）

#### commit を要求された場合（例: "commit して", "コミットして", "commit this"）
1. 変更内容を確認する（`git status` / `git diff`）
2. ファイルをステージする（`git add`） — すでにステージ済みの場合はスキップ
3. 上記のコミットメッセージ形式に従ってコミットする

#### PR 作成を要求された場合（例: "pr作成して", "PR作って", "create a PR"）
1. 現在の変更内容とブランチ構成を確認する
2. 現在のブランチから新しいブランチを作成する（命名規則: `fix/`, `feat/`, `style/` のプレフィックス）
3. 上記のコミットメッセージ形式に従ってコミットする
4. 新しいブランチをリモートへプッシュする
5. 元のブランチに対してプルリクエストを作成する（下記の PR 品質基準に従うこと）
6. 元のブランチに戻る
7. マージ後に作業ブランチの削除を提案する

### プルリクエスト品質基準
1. 最新コミットだけでなく、全コミット履歴を分析する
2. `git diff [base-branch]...HEAD` を使用してすべての変更を確認する
3. 日本語で包括的な PR サマリーを作成する
4. TODO を含むテスト計画を記載する
5. 新規ブランチの場合は `-u` フラグ付きでプッシュする
6. デフォルトブランチへの直接プッシュが不適切な場合は、PR 作成を提案する

### 機能実装ワークフロー
1. **まず計画する**
   - 実装計画を作成する
   - 依存関係とリスクを特定する
   - フェーズに分割する

2. **TDD アプローチ**
   - 最初にテストを書く（RED）
   - テストをパスする実装を行う（GREEN）
   - リファクタリングする（IMPROVE）
   - カバレッジ 80% 以上を確認する

3. **コードレビュー**
   - 記述後にコードをレビューする
   - CRITICAL および HIGH の問題に対処する
   - 可能な場合は MEDIUM の問題も修正する

4. **コミットとプッシュ**
   - 詳細なコミットメッセージを記述する
   - Conventional Commits 形式に従う

## 4. セーフティガード
- ファイル編集・依存追加・外部通信は、プロジェクトの既定ルールに従うこと
- 危険と判断した操作は実行前にユーザーに確認を求めること
- ブラウザ自動化の対象サイトは最小限に限定し、個人情報や秘密情報を扱わない

## 5. コーディングスタイル
### イミュータビリティ（重要）
常に新しいオブジェクトを作成し、決してミューテーションしないこと:

```javascript
// 誤り: ミューテーション
function updateUser(user, name) {
  user.name = name; // ミューテーション!
  return user;
}

// 正しい: イミュータブル
function updateUser(user, name) {
  return {
    ...user,
    name,
  };
}
```

### ファイル構成

多数の小さなファイル > 少数の大きなファイル:

- 高凝集・低結合
- 通常 200〜400 行、最大 800 行
- 大きなコンポーネントからユーティリティを抽出する
- 種類別ではなく、機能・ドメイン別に整理する

### エラーハンドリング
常にエラーを包括的に処理すること:

```typescript
try {
  const result = await riskyOperation();
  return result;
} catch (error) {
  console.error("Operation failed:", error);
  throw new Error("詳細でユーザーにわかりやすいメッセージ");
}
```

### 入力バリデーション
常にユーザー入力を検証すること:

```typescript
import { z } from "zod";

const schema = z.object({
  email: z.string().email(),
  age: z.number().int().min(0).max(150),
});

const validated = schema.parse(input);
```

### コード品質チェックリスト
作業完了とする前に:

- [ ] コードが読みやすく、適切に命名されている
- [ ] 関数が小さい（50 行未満）
- [ ] ファイルが焦点を絞っている（800 行未満）
- [ ] ネストが深くない（4 階層以下）
- [ ] 適切なエラーハンドリングがある
- [ ] console.log 文がない
- [ ] ハードコードされた値がない
- [ ] ミューテーションがない（イミュータブルなパターンを使用）

## 6. セキュリティ
### 必須セキュリティチェック
いかなるコミットの前にも:

- [ ] ハードコードされた秘密情報がない（API キー、パスワード、トークン）
- [ ] すべてのユーザー入力が検証されている
- [ ] SQL インジェクションの防止（パラメータ化クエリ）
- [ ] XSS の防止（HTML のサニタイズ）
- [ ] CSRF 保護が有効
- [ ] 認証・認可が検証されている
- [ ] すべてのエンドポイントにレート制限がある
- [ ] エラーメッセージが機密情報を漏洩しない

### シークレット管理
```typescript
// 禁止: ハードコードされた秘密情報
const apiKey = "sk-proj-xxxxx";

// 必須: 環境変数
const apiKey = process.env.OPENAI_API_KEY;

if (!apiKey) {
  throw new Error("OPENAI_API_KEY が設定されていません");
}
```

### セキュリティ対応プロトコル
セキュリティ問題を発見した場合:

1. 直ちに停止する
2. セキュリティの根本原因を分析する
3. 続行する前に CRITICAL な問題を修正する
4. 露出した秘密情報をローテーションする
5. コードベース全体を類似の問題についてレビューする

## 7. テスト要件
### 最小テストカバレッジ: 80%

テスト種別（すべて必須）:

1. **ユニットテスト** — 個々の関数、ユーティリティ、コンポーネント
2. **統合テスト** — API エンドポイント、データベース操作
3. **E2E テスト** — クリティカルなユーザーフロー（Playwright）

### テスト駆動開発
必須ワークフロー:

1. 最初にテストを書く（RED）
2. テストを実行する — 失敗するはず
3. 最小限の実装を書く（GREEN）
4. テストを実行する — パスするはず
5. リファクタリングする（IMPROVE）
6. カバレッジを確認する（80% 以上）

### テスト失敗のトラブルシューティング
1. テストの独立性を確認する
2. モックが正しいか検証する
3. テストではなく実装を修正する（テストが間違っている場合を除く）

## 8. 共通パターン
### API レスポンス形式
```typescript
interface ApiResponse<T> {
  success: boolean;
  data?: T;
  error?: string;
  meta?: {
    total: number;
    page: number;
    limit: number;
  };
}
```

### カスタムフックのパターン
```typescript
export function useDebounce<T>(value: T, delay: number): T {
  const [debouncedValue, setDebouncedValue] = useState<T>(value);

  useEffect(() => {
    const handler = setTimeout(() => setDebouncedValue(value), delay);
    return () => clearTimeout(handler);
  }, [value, delay]);

  return debouncedValue;
}
```

### リポジトリパターン
```typescript
interface Repository<T> {
  findAll(filters?: Filters): Promise<T[]>;
  findById(id: string): Promise<T | null>;
  create(data: CreateDto): Promise<T>;
  update(id: string, data: UpdateDto): Promise<T>;
  delete(id: string): Promise<void>;
}
```

### スケルトンプロジェクト
新機能を実装する際は:

1. 実績のあるスケルトンプロジェクトを探す
2. 選択肢を評価する（セキュリティ、拡張性、関連性）
3. 最適なものをクローンして基盤とする
4. 実績のある構造の中で反復開発する
