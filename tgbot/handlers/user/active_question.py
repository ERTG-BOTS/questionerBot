import datetime
import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove

from infrastructure.database.models import Question, User
from infrastructure.database.repo.requests import RequestsRepo
from tgbot.config import load_config
from tgbot.filters.active_question import ActiveQuestion, ActiveQuestionWithCommand
from tgbot.keyboards.user.main import (
    QuestionQualitySpecialist,
    closed_dialog_kb,
    dialog_quality_kb,
)
from tgbot.misc import dicts
from tgbot.misc.helpers import check_premium_emoji
from tgbot.services.logger import setup_logging
from tgbot.services.scheduler import (
    restart_inactivity_timer,
    run_delete_timer,
    stop_inactivity_timer,
)

user_q_router = Router()

config = load_config(".env")

setup_logging()
logger = logging.getLogger(__name__)


@user_q_router.message(ActiveQuestionWithCommand("end"))
async def active_question_end(
    message: Message, repo: RequestsRepo, user: User, active_dialog_token: str = None
):
    question: Question = await repo.questions.get_question(token=active_dialog_token)

    if question is not None:
        if question.Status != "closed":
            # Останавливаем таймер неактивности
            stop_inactivity_timer(question.Token)

            await repo.questions.update_question_status(
                token=question.Token, status="closed"
            )
            await repo.questions.update_question_end(
                token=question.Token, end_time=datetime.datetime.now()
            )

            if question.QualityDuty is not None:
                if question.QualityDuty:
                    await message.bot.send_message(
                        chat_id=config.tg_bot.forum_id,
                        message_thread_id=question.TopicId,
                        text=f"""<b>🔒 Вопрос закрыт</b>
    
Специалист <b>{user.FIO}</b> закрыл вопрос
👍 Специалист <b>не мог решить вопрос самостоятельно</b>""",
                        reply_markup=dialog_quality_kb(
                            token=question.Token,
                            role="duty",
                            show_quality=None,
                            allow_return=question.AllowReturn,
                        ),
                    )
                else:
                    await message.bot.send_message(
                        chat_id=config.tg_bot.forum_id,
                        message_thread_id=question.TopicId,
                        text=f"""<b>🔒 Вопрос закрыт</b>

Специалист <b>{user.FIO}</b> закрыл вопрос
👎 Специалист <b>мог решить вопрос самостоятельно</b>""",
                        reply_markup=dialog_quality_kb(
                            token=question.Token,
                            role="duty",
                            show_quality=None,
                            allow_return=question.AllowReturn,
                        ),
                    )
            else:
                await message.bot.send_message(
                    chat_id=config.tg_bot.forum_id,
                    message_thread_id=question.TopicId,
                    text=f"""<b>🔒 Вопрос закрыт</b>

Специалист <b>{user.FIO}</b> закрыл вопрос
Оцени, мог ли специалист решить его самостоятельно""",
                    reply_markup=dialog_quality_kb(
                        token=question.Token,
                        role="duty",
                        show_quality=True,
                        allow_return=question.AllowReturn,
                    ),
                )

            await message.bot.edit_forum_topic(
                chat_id=config.tg_bot.forum_id,
                message_thread_id=question.TopicId,
                name=question.Token,
                icon_custom_emoji_id=dicts.topicEmojis["closed"],
            )
            await message.bot.close_forum_topic(
                chat_id=config.tg_bot.forum_id, message_thread_id=question.TopicId
            )

            await message.reply(
                text="<b>🔒 Вопрос закрыт</b>", reply_markup=ReplyKeyboardRemove()
            )
            await message.answer(
                """Ты закрыл вопрос
Оцени, помогли ли тебе решить вопрос""",
                reply_markup=dialog_quality_kb(token=question.Token, role="employee"),
            )

            logger.info(
                f"[Вопрос] - [Закрытие] Пользователь {message.from_user.username} ({message.from_user.id}): Закрыт вопрос {question.Token} со старшим {question.TopicDutyFullname}"
            )
        elif question.Status == "closed":
            await message.reply("<b>🔒 Вопрос был закрыт</b>")
            await message.bot.close_forum_topic(
                chat_id=config.tg_bot.forum_id, message_thread_id=question.TopicId
            )
            logger.info(
                f"[Вопрос] - [Закрытие] Пользователь {message.from_user.username} ({message.from_user.id}): Неудачная попытка закрытия вопроса {question.Token} со старшим {question.TopicDutyFullname}. Вопрос уже закрыт"
            )

    else:
        await message.answer("""<b>⚠️ Ошибка</b>

Не удалось найти вопрос в базе""")
        logger.error(
            f"[Вопрос] - [Закрытие] Пользователь {message.from_user.username} ({message.from_user.id}): Попытка закрытия вопроса неуспешна. Не удалось найти вопрос в базе с TopicId = {message.message_id}"
        )


@user_q_router.message(ActiveQuestion())
async def active_question(
    message: Message, active_dialog_token: str, repo: RequestsRepo
) -> None:
    question: Question = await repo.questions.get_question(token=active_dialog_token)

    if message.text == "✅️ Закрыть вопрос":
        await active_question_end(
            message=message, repo=repo, active_dialog_token=active_dialog_token
        )
        return

    # Перезапускаем таймер неактивности при сообщении от пользователя
    if config.tg_bot.activity_status:
        restart_inactivity_timer(
            question_token=question.Token, bot=message.bot, repo=repo
        )

    await message.bot.copy_message(
        from_chat_id=message.chat.id,
        message_id=message.message_id,
        chat_id=config.tg_bot.forum_id,
        message_thread_id=question.TopicId,
    )

    # Уведомление о премиум эмодзи
    have_premium_emoji, emoji_ids = await check_premium_emoji(message)
    if have_premium_emoji and emoji_ids:
        emoji_sticker_list = await message.bot.get_custom_emoji_stickers(emoji_ids)

        sticker_info = []
        for emoji_sticker in emoji_sticker_list:
            sticker_info.append(f"{emoji_sticker.emoji}")

        stickers_text = "".join(sticker_info)

        emoji_message = await message.reply(f"""<b>💎 Премиум эмодзи</b>

Сообщение содержит премиум эмодзи, собеседник увидит бесплатные аналоги: {stickers_text}

<i>Предупреждение удалится через 30 секунд</i>""")
        await run_delete_timer(
            bot=message.bot,
            chat_id=message.chat.id,
            message_ids=[emoji_message.message_id],
            seconds=30,
        )

    logger.info(
        f"[Вопрос] - [Общение] Токен: {question.Token} | Специалист: {question.EmployeeFullname} | Сообщение: {message.text}"
    )


@user_q_router.callback_query(
    QuestionQualitySpecialist.filter(F.return_question == False)
)
async def dialog_quality_employee(
    callback: CallbackQuery,
    callback_data: QuestionQualitySpecialist,
    repo: RequestsRepo,
):
    question: Question = await repo.questions.get_question(token=callback_data.token)
    await repo.questions.update_question_quality(
        token=callback_data.token, quality=callback_data.answer, is_duty=False
    )

    await callback.answer("Оценка успешно выставлена ❤️")
    if callback_data.answer:
        await callback.message.edit_text(
            """Ты поставил оценку:
👍 Старший <b>помог решить твой вопрос</b>""",
            reply_markup=closed_dialog_kb(token=callback_data.token, role="employee"),
        )
    else:
        await callback.message.edit_text(
            """Ты поставил оценку:
👎 Старший <b>не помог решить твой вопрос</b>""",
            reply_markup=closed_dialog_kb(token=callback_data.token, role="employee"),
        )
    logger.info(
        f"[Вопрос] - [Оценка] Пользователь {callback.from_user.username} ({callback.from_user.id}): Выставлена оценка {callback_data.answer} вопросу {question.Token} от специалиста"
    )

