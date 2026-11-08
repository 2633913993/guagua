// node scripts/check-weixin.cjs [微信编译产物目录]
const fs = require('fs')
const path = require('path')
const assert = require('assert')
const output = path.resolve(process.argv[2] || 'unpackage/dist/build/mp-weixin')
const files = []
function walk(dir) {
  for (const item of fs.readdirSync(dir, { withFileTypes: true })) {
    const file = path.join(dir, item.name)
    if (item.isDirectory()) walk(file)
    else files.push(file)
  }
}
walk(output)
const app = JSON.parse(fs.readFileSync(path.join(output, 'app.json'), 'utf8'))
const project = JSON.parse(fs.readFileSync(path.join(output, 'project.config.json'), 'utf8'))
assert.equal(app.lazyCodeLoading, 'requiredComponents', '未启用组件按需注入')
assert.equal(project.setting.minified, true, '未启用 JS 压缩')
assert.equal(project.setting.minifyWXML, true, '未启用 WXML 压缩')
assert.equal(project.setting.minifyWXSS, true, '未启用 WXSS 压缩')
// 此项目没有分包，汇总产物大小比微信实际计入的主包大小更保守。
const total = files.reduce((sum, file) => sum + fs.statSync(file).size, 0)
assert(total < 1.5 * 1024 * 1024, '产物超过 1.5 MiB')
const resources = files.filter(file => /\.(png|jpe?g|webp|gif|mp3|mp4|wav|aac)$/i.test(file))
for (const file of resources) assert(fs.statSync(file).size <= 200 * 1024, '资源过大：' + file)
assert(!files.some(file => /frog-atlas\.png$/.test(file)), '完整设计图不应进入编译包')
for (const file of files.filter(file => /\.js$/.test(file))) {
  const source = fs.readFileSync(file, 'utf8')
  assert(!/\beval\s*\(/.test(source), '编译包中包含 eval 调试脚本：' + file)
}
console.log(`PASS: total ${(total / 1024).toFixed(1)} KiB; lazy loading; JS/WXML/WXSS compression; ${resources.length} resources <= 200 KiB; no eval wrappers`)
if (!project.appid || project.appid === 'touristappid') console.log('待配置：请在 manifest.json 的 mp-weixin.appid 填写实际微信 AppID，再重新编译。')
