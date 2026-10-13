// 假的 edge-tts：FAKE_EDGE=ok 吐一段音频；否则像 10-13 线上那样 403 退出、一个字节不吐
if (process.env.FAKE_EDGE === "ok") {
  process.stdout.write(Buffer.from("fake-edge-audio-" + "e".repeat(200)));
  process.exit(0);
}
process.stderr.write("aiohttp.client_exceptions.WSServerHandshakeError: 403, message='Invalid response status'\n");
process.exit(1);
