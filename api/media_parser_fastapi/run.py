"""python run.py starts local tests; Docker uses --production explicitly."""
import argparse
import uvicorn


def main():
    cli = argparse.ArgumentParser(description='呱呱去水印后端')
    cli.add_argument('--production', action='store_true', help='微信云托管正式模式')
    args = cli.parse_args()
    if args.production:
        uvicorn.run('app.main:app', host='0.0.0.0', port=8080, reload=False)
    else:
        # No host override: cannot accidentally expose the local test identity.
        from local_dev import create_local_app
        print('本地调试：http://127.0.0.1:8080/docs；固定测试账号 wxTestApp / openidA', flush=True)
        uvicorn.run(create_local_app(), host='127.0.0.1', port=8080,
                    reload=False, proxy_headers=False)


if __name__ == '__main__':
    main()
