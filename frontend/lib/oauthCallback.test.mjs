import assert from 'node:assert/strict';
import { BRICKS_GO_AUTH_CALLBACK, codeFromAuthCallback } from './oauthCallback.js';

const ok = `${BRICKS_GO_AUTH_CALLBACK}?code=pkce-code`;
assert.equal(codeFromAuthCallback(ok, BRICKS_GO_AUTH_CALLBACK), 'pkce-code');

assert.equal(
  codeFromAuthCallback('https://evil.example/auth-callback?code=stolen', BRICKS_GO_AUTH_CALLBACK),
  null,
);
assert.equal(
  codeFromAuthCallback('bricks-go://other?code=nope', BRICKS_GO_AUTH_CALLBACK),
  null,
);
assert.equal(
  codeFromAuthCallback(`${BRICKS_GO_AUTH_CALLBACK}#access_token=implicit`, BRICKS_GO_AUTH_CALLBACK),
  null,
);
assert.equal(
  codeFromAuthCallback(`${BRICKS_GO_AUTH_CALLBACK}?error=access_denied&code=still`, BRICKS_GO_AUTH_CALLBACK),
  null,
);

const exp = 'exp://127.0.0.1:8081/--/auth-callback';
assert.equal(codeFromAuthCallback(`${exp}?code=dev`, exp), 'dev');
assert.equal(codeFromAuthCallback(`${exp}?code=dev`, BRICKS_GO_AUTH_CALLBACK), null);

console.log('oauthCallback tests passed');
