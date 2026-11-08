<template>
	<view>正在打开首页…</view>
</template>
<script>
	import {
		captureShareEntry
	} from '../../services/share.js'
	export default {
		onLoad(options = {}) {
			captureShareEntry(options)
			const query = Object.keys(options).map(key => `${encodeURIComponent(key)}=${encodeURIComponent(options[key])}`)
				.join('&')
			uni.redirectTo({
				url: '/pages/home/index' + (query ? '?' + query : '')
			})
		}
	}
</script>