<template>
	<view class="invite-page">
		<view class="summary">
			<text class="heading">分享好内容，也分享好心情</text>
			<view class="stats"><text>{{ total }} 位好友</text><text>累计奖励 {{ totalReward }} 次</text></view>
			<text
				class="hint">{{ rewardPerFriend > 0 ? '好友每次通过分享进入，邀请人 +' + rewardPerFriend + ' 次；同一好友重新打开可再次奖励，网络重试不重复奖励，奖励当日有效。' : '当前只记录分享进入，不发放次数。' }}</text>
			<button class="share" open-type="share" :disabled="!shareReady">分享给好友</button>
		</view>
		<view v-if="error" class="state" @tap="reload">{{ error }}，点击重试</view>
		<view v-else-if="loading && !items.length" class="state">正在读取邀请记录…</view>
		<view v-else-if="!items.length" class="state">还没有好友通过你的分享进入，分享给朋友试试吧</view>
		<view v-for="item in items" :key="item.id" class="record">
			<view class="record-title"><text>{{ item.visitor_name }}</text><text
					class="reward">{{ item.reward_amount > 0 ? '+' + item.reward_amount + ' 次' : '已记录' }}</text></view>
			<text class="time">首次进入：{{ formatTime(item.created_at) }}</text>
			<text class="time">最近进入：{{ formatTime(item.last_seen_at) }}</text>
		</view>
		<button v-if="nextCursor && !error" class="more" :disabled="loading"
			@tap="loadMore">{{ loading ? '正在加载…' : '加载更多' }}</button>
		<text class="privacy">仅显示本站好友编号和进入时间，不采集微信昵称、头像或手机号。累计奖励含历史记录，当前可用次数以首页接口为准。</text>
	</view>
</template>
<script>
	import {
		prepareShare,
		shareMessage,
		fetchInvitees
	} from '../../services/share.js'
	export default {
		data() {
			return {
				items: [],
				total: 0,
				totalReward: 0,
				rewardPerFriend: 0,
				nextCursor: null,
				loading: false,
				error: '',
				shareReady: false
			}
		},
		onShow() {
			this._disposed = false;
			this.reload()
		},
		onUnload() {
			this._disposed = true;
			this._generation = (this._generation || 0) + 1
		},
		async onPullDownRefresh() {
			try {
				await this.reload()
			} finally {
				uni.stopPullDownRefresh()
			}
		},
		onReachBottom() {
			this.loadMore()
		},
		onShareAppMessage() {
			return shareMessage()
		},
		methods: {
			formatTime(value) {
				return value.replace('T', ' ').replace(/\.\d+/, '').replace('+08:00', '（北京时间）')
			},
			async reload() {
				const generation = this._generation = (this._generation || 0) + 1
				this.loading = true
				this.error = ''
				// 两个请求分别处理，分享配置失败不会吞掉列表结果。
				prepareShare().then(profile => {
					if (!this._disposed && generation === this._generation) {
						this.shareReady = true;
						this.rewardPerFriend = profile.reward_per_friend
					}
				}).catch(() => {
					if (!this._disposed && generation === this._generation) this.shareReady = false
				})
				try {
					const data = await fetchInvitees()
					if (this._disposed || generation !== this._generation) return
					this.items = data.items
					this.total = data.total
					this.totalReward = data.total_reward
					this.rewardPerFriend = data.reward_per_friend
					this.nextCursor = data.next_cursor
				} catch (error) {
					if (!this._disposed && generation === this._generation) this.error = error.message || '记录读取失败'
				} finally {
					if (!this._disposed && generation === this._generation) this.loading = false
				}
			},
			async loadMore() {
				if (this.loading || !this.nextCursor) return
				const generation = this._generation
				this.loading = true
				try {
					const data = await fetchInvitees(this.nextCursor)
					if (this._disposed || generation !== this._generation) return
					this.items = [...this.items, ...data.items]
					this.nextCursor = data.next_cursor
				} catch (error) {
					if (!this._disposed) uni.showToast({
						title: '加载失败，请重试',
						icon: 'none'
					})
				} finally {
					if (!this._disposed && generation === this._generation) this.loading = false
				}
			}
		}
	}
</script>
<style>
	page {
		background: #f5faf6;
		color: #123b31;
	}

	.invite-page {
		padding: 28rpx;
	}

	.summary,
	.record {
		background: #fff;
		border: 1rpx solid #e7f1e4;
		border-radius: 28rpx;
		padding: 30rpx;
		margin-bottom: 22rpx;
	}

	.heading {
		font-size: 32rpx;
		font-weight: 700;
	}

	.stats,
	.record-title {
		display: flex;
		justify-content: space-between;
		margin: 20rpx 0;
	}

	.stats {
		font-size: 30rpx;
		color: #16834e;
		font-weight: 700;
	}

	.hint,
	.time,
	.privacy {
		display: block;
		font-size: 23rpx;
		color: #73877c;
		line-height: 1.8;
	}

	.share {
		margin-top: 24rpx;
		border-radius: 20rpx;
		color: #fff;
		background: #1da266;
		font-size: 28rpx;
	}

	.reward {
		color: #16834e;
		font-size: 25rpx;
	}

	.state {
		text-align: center;
		padding: 50rpx 20rpx;
		color: #73877c;
	}

	.more {
		color: #16834e;
		background: #fff;
		font-size: 27rpx;
	}

	.privacy {
		margin-top: 30rpx;
	}
</style>