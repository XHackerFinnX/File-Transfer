"""Regression: slow shop I/O must leave the game's event loop available.

Called with the isolated test environment; all external services are mocked.
"""
import asyncio
import time
from unittest.mock import AsyncMock, patch

from routers import tilda_apex_webhook as shop
from services.volleyball import Room, Seat, VolleyballServer


def slow(value=None):
    def call(*args, **kwargs):
        time.sleep(.12)
        return value
    return call


async def check():
    game=VolleyballServer()
    room=Room('1234','test')
    room.seats=[Seat(i,room,b'',None,0,disconnected_at=time.monotonic()) for i in range(2)]
    room.match.start()
    game.rooms[room.code]=room
    await game.start()
    async def during(operation):
        before=room.match.tick
        result=await operation
        assert room.match.tick-before >= 2, 'Blocking shop I/O froze the game simulation'
        return result
    try:
        with patch.object(shop,'_cdek_account_or_404',return_value='test'), \
             patch.object(shop,'_cdek_im_number_prefix',return_value='shop_'), \
             patch.object(shop,'cdek_get_token',side_effect=slow('test-token')), \
             patch.object(shop,'find_cdek_order',side_effect=slow({'uuid':'test-uuid'})), \
             patch.object(shop,'update_cdek_order_number',side_effect=slow('request-uuid')), \
             patch.object(shop,'update_tilda_submission_im_number',new_callable=AsyncMock) as update, \
             patch.object(shop,'_site_name_or_404',return_value='shop'), \
             patch.object(shop,'_form_secret_or_403'), \
             patch.object(shop,'_database_target_or_404',return_value='test-db'):
            value=await during(shop._auto_update_cdek_im_number('test-db','shop','submission',{'orderid':'42'}))
            assert value=='shop_42'
            update.assert_awaited_once_with('test-db','shop','submission','42','shop_42')
            response=await during(shop.tilda_update_cdek_im_number('shop',None,shop.CdekImNumberUpdateRequest(current_im_number='old',new_im_number='new')))
            assert response.status_code==200
        with patch.object(shop,'send_customer_email',side_effect=slow()), \
             patch.object(shop,'_build_order_email',return_value=('subject','body','html')), \
             patch.object(shop,'save_tilda_email_message',new_callable=AsyncMock,return_value={'id':'sent'}) as save, \
             patch.object(shop,'_site_name_or_404',return_value='shop'), \
             patch.object(shop,'_form_secret_or_403'), \
             patch.object(shop,'_database_target_or_404',return_value='test-db'):
            await during(shop._send_order_notification_for_submission('test-db','shop','submission',{'email':'test@example.invalid','orderid':'42'}))
            response=await during(shop.tilda_send_email('shop',None,shop.TildaEmailSendRequest(submission_id='submission',to_email='test@example.invalid',message_type='custom_message',custom_body='test body')))
            assert response.status_code==200
            assert save.await_count==2
    finally:
        await game.stop()


if __name__=='__main__':
    asyncio.run(check())
