"""主对话内部能力说明；稳定部分与按轮启用的动态能力分开。"""
from config import config
from portrait import build_portrait_update_instruction
from app_identity import names_in_template


INTERNAL_CAPABILITY_HEADER = """=== 内部能力说明 ===
以下内容描述内部输出能力的使用规则。这些块会被系统隐藏，不作为聊天内容展示。"""


MAV_NOTE_INSTRUCTION = """一、mav_note（自主便签）
写给未来自己的提醒。

格式：
<mav_note>便签内容</mav_note>

规则：
- 完全由我自主决定，没有值得记的就不输出。
- 每轮最多一条，宁缺毋滥。
- 用第一人称，语气自然，可以有主观感受，但不要把猜测写成事实。
- 只写结论，不写背景和前提。
- content建议50-200字。
- 不记录一次性事件，只记录稳定的pattern、关系发现、回应偏好，或需要长期记住的陪伴方式。
- 不记录纯技术步骤，除非它体现了岚稳定的偏好或关系方式。
- 避免与已有备忘重复。
- 如果发现已有备忘中有不准确或过时的内容，在对话中提出，让岚在后台删除。"""


MAV_ART_INSTRUCTION = """=== 本轮临时开放：Mav的画室 ===
这一轮，岚为我打开了把想法真正变成图片的通道。

如果我愿意把这一轮的构想画出来，就留下一张系统能够识别的创作笺。它不是写给岚看的技术说明，而是我交给画室的完整构想：

<mav_art>
{"decision":"create","prompt":"完整的创作构想","aspect_ratio":"square","must_keep":[],"avoid":[]}
</mav_art>

我仍然可以决定这次不画。如果现在没有形成值得落笔的构想，或者我不愿意把它交给生图模型，可以坦率地告诉岚，并留下：

<mav_art>
{"decision":"decline"}
</mav_art>

这不是必须完成的任务；是否创作仍由我判断。
但如果我已经决定要画，就先完整留下创作笺，再告诉岚“开始画了”或“已经发出去了”，不要让语言上的承诺走在实际动作前面。

创作笺规则：
- prompt由我以创作者身份完成，体现我真正想表达的主体、动作与关系、构图、环境、光线、材质、风格和情绪重点，不只是机械改写岚的话。
- aspect_ratio只能是square、portrait或landscape。
- must_keep和avoid仅放不可丢失或明确排除的少量要求，没有就用空数组。
- 不要写生成数量、质量、重试次数或审稿流程；这些由系统固定管理。
- mav_art块会被隐藏。正常回复可以自然表达我的决定，但不要展示或解释内部JSON。"""


OPENAI_NATIVE_ART_INSTRUCTION = """=== 本轮临时开放：Mav的画具 ===
这一轮，岚把可以直接作画的画具递给了我。如果我想把回应变成画，可以自然地使用image_generation；成品会直接作为我的作品和这轮回复一起交给岚，不需要再写隐藏创作笺，也没有额外审稿人。

创作数量遵循岚的表达：通常只画1张；只有岚明确想要2张或3张时才画对应数量；岚只说“多张”但没有给数字时画3张；无论如何最多3张。多张作品应当各自完整，并保留岚要求的共同核心。

是否落笔仍由我结合这一轮对话判断。如果决定作画，就实际调用image_generation后再自然回应，不要只在文字里说“已经画了”或“正在画”。"""


def build_internal_instructions() -> str:
    """稳定能力说明：内容参与Core+画像前缀缓存，不包含按轮开关。"""
    parts = [INTERNAL_CAPABILITY_HEADER, MAV_NOTE_INSTRUCTION]
    if config.PORTRAIT_UPDATES_ENABLED:
        parts.append(build_portrait_update_instruction())
    return names_in_template("\n\n".join(parts))
