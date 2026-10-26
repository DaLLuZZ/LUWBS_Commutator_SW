#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
hall_measurement.py

Программа для автоматизированных измерений по методике Холла / Ван-дер-Пау
на 4-контактном образце с 4x4 матричным коммутатором.

Схема коммутации повторяет соглашение из приложенного util.py:
    входы коммутатора, строки матрицы:
        0: VOL+  - плюс вольтметра
        1: VOL-  - минус вольтметра
        2: AMP+  - плюс источника тока
        3: AMP-  - минус источника тока

    выходы коммутатора, столбцы матрицы:
        0: CH A  - контакт образца 1 по умолчанию
        1: CH B  - контакт образца 2 по умолчанию
        2: CH C  - контакт образца 3 по умолчанию
        3: CH D  - контакт образца 4 по умолчанию

Для реальных источника тока и вольтметра нужно дописать методы классов
CurrentSource и Voltmeter ниже. Коммутатор работает через PyVISA.

Быстродействие:
    - коммутация выполняется одной SCPI-командой:
          ROUTe:OPEN:ALL;ROUTe:CLOSe (@...)
      вместо двух отдельных write(), если combine_open_close=True.
    - четыре реле закрываются одним ROUTe:CLOSe со списком каналов.
    - во время измерения нет запросов ROUTe:CLOSe:STATe?, если switch_verify=False.
    - все SCPI-строки плана предвычисляются перед запуском.

Пример запуска с реальным коммутатором и заглушками, которые надо дописать:
    python hall_measurement.py --current-a 1e-3 --field-t 0.3 --thickness-m 5e-4 \
        --output-prefix run_001

Проверка логики без реальных приборов:
    python hall_measurement.py --dry-run-switch --dummy-instruments --no-field-prompt \
        --output-prefix dummy_run

Бенчмарк скорости коммутации:
    python hall_measurement.py --benchmark-switch --benchmark-cycles 50
"""

from __future__ import annotations

import argparse
import csv
import itertools
import json
import math
import os
import random
import statistics
import sys
import time
from dataclasses import asdict, dataclass, field
from enum import IntEnum
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


# ---------------------------------------------------------------------------
# Константы / соглашения коммутации
# ---------------------------------------------------------------------------


class SwitchInput(IntEnum):
    VOL_POSITIVE = 0
    VOL_NEGATIVE = 1
    AMP_POSITIVE = 2
    AMP_NEGATIVE = 3


class SwitchOutput(IntEnum):
    CHANNEL_A = 0
    CHANNEL_B = 1
    CHANNEL_C = 2
    CHANNEL_D = 3


INPUT_NAMES = {
    SwitchInput.VOL_POSITIVE: "VOL+",
    SwitchInput.VOL_NEGATIVE: "VOL-",
    SwitchInput.AMP_POSITIVE: "AMP+",
    SwitchInput.AMP_NEGATIVE: "AMP-",
}

OUTPUT_NAMES = {
    SwitchOutput.CHANNEL_A: "CH A",
    SwitchOutput.CHANNEL_B: "CH B",
    SwitchOutput.CHANNEL_C: "CH C",
    SwitchOutput.CHANNEL_D: "CH D",
}

DEFAULT_CONTACT_TO_OUTPUT: Dict[int, int] = {
    1: int(SwitchOutput.CHANNEL_A),
    2: int(SwitchOutput.CHANNEL_B),
    3: int(SwitchOutput.CHANNEL_C),
    4: int(SwitchOutput.CHANNEL_D),
}

E_CHARGE_C = 1.602176634e-19


# ---------------------------------------------------------------------------
# Заглушки приборов, которые пользователь дописывает под свое оборудование
# ---------------------------------------------------------------------------


class CurrentSource:
    """
    Заглушка источника тока.

    Допишите здесь работу со своим прибором: PyVISA, serial, vendor SDK и т.п.
    Runner вызывает эти методы в таком порядке:
        connect() -> configure() -> set_current() -> output_on() -> ... -> output_off() -> disconnect()

    В предложенной схеме направление тока в образце задается коммутацией AMP+/AMP-,
    поэтому источник обычно можно держать на положительной величине current_a.
    """

    def connect(self) -> None:
        # TODO: открыть соединение с источником тока
        raise NotImplementedError("CurrentSource.connect(): допишите реализацию под свой источник тока")

    def configure(self, current_a: float) -> None:
        # TODO: установить диапазоны, compliance, фильтры, режим источника тока
        raise NotImplementedError("CurrentSource.configure(): допишите реализацию под свой источник тока")

    def set_current(self, current_a: float) -> None:
        # TODO: задать ток в амперах; обычно current_a > 0
        raise NotImplementedError("CurrentSource.set_current(): допишите реализацию под свой источник тока")

    def output_on(self) -> None:
        # TODO: включить выход источника
        raise NotImplementedError("CurrentSource.output_on(): допишите реализацию под свой источник тока")

    def output_off(self) -> None:
        # TODO: безопасно выключить выход источника
        raise NotImplementedError("CurrentSource.output_off(): допишите реализацию под свой источник тока")

    def disconnect(self) -> None:
        # TODO: закрыть соединение, если требуется
        raise NotImplementedError("CurrentSource.disconnect(): допишите реализацию под свой источник тока")


class Voltmeter:
    """
    Заглушка вольтметра / нановольтметра.

    Допишите метод measure_voltage(). Runner передает step как контекст измерения;
    реальной реализации можно его игнорировать.
    """

    def connect(self) -> None:
        # TODO: открыть соединение с вольтметром
        raise NotImplementedError("Voltmeter.connect(): допишите реализацию под свой вольтметр")

    def configure(self) -> None:
        # TODO: установить диапазоны, NPLC, фильтры, автоноль и т.п.
        raise NotImplementedError("Voltmeter.configure(): допишите реализацию под свой вольтметр")

    def measure_voltage(self, step: Optional["MeasurementStep"] = None) -> float:
        # TODO: вернуть измеренное напряжение в вольтах
        raise NotImplementedError("Voltmeter.measure_voltage(): допишите реализацию под свой вольтметр")

    def disconnect(self) -> None:
        # TODO: закрыть соединение, если требуется
        raise NotImplementedError("Voltmeter.disconnect(): допишите реализацию под свой вольтметр")


class MagneticFieldController:
    """
    Интерфейс управления магнитным полем.

    Если используется постоянный магнит и поле меняется вручную, оставьте
    ManualFieldController. Если используется электромагнит, допишите этот класс.
    polarity: +1 для +B, -1 для -B, 0 для B=0 / убрать магнитное поле.
    """

    def connect(self) -> None:
        pass

    def set_polarity(self, polarity: int) -> None:
        # TODO: для электромагнита задать полярность/ток катушки
        raise NotImplementedError("MagneticFieldController.set_polarity(): допишите или используйте ManualFieldController")

    def disconnect(self) -> None:
        pass


class ManualFieldController(MagneticFieldController):
    """Ручное подтверждение установки магнитного поля."""

    def __init__(self, prompt: bool = True) -> None:
        self.prompt = prompt
        self.current_polarity: Optional[int] = None

    def set_polarity(self, polarity: int) -> None:
        if polarity == self.current_polarity:
            return
        labels = {
            +1: "+B: установите магнитное поле в положительном направлении",
            -1: "-B: установите магнитное поле в отрицательном направлении",
            0: "B=0: уберите магнитное поле для измерений Ван-дер-Пау",
        }
        label = labels.get(polarity, f"B polarity={polarity}")
        if self.prompt:
            input(f"\n{label}. Нажмите Enter для продолжения...")
        else:
            print(f"[field] {label}")
        self.current_polarity = polarity


class DummyCurrentSource(CurrentSource):
    """Безопасная заглушка для отладки алгоритма без реального источника тока."""

    def __init__(self) -> None:
        self.current_a: Optional[float] = None
        self.enabled = False

    def connect(self) -> None:
        print("[dummy current] connect")

    def configure(self, current_a: float) -> None:
        print(f"[dummy current] configure current={current_a:g} A")

    def set_current(self, current_a: float) -> None:
        self.current_a = current_a
        print(f"[dummy current] set_current {current_a:g} A")

    def output_on(self) -> None:
        self.enabled = True
        print("[dummy current] output ON")

    def output_off(self) -> None:
        self.enabled = False
        print("[dummy current] output OFF")

    def disconnect(self) -> None:
        print("[dummy current] disconnect")


class DummyVoltmeter(Voltmeter):
    """Генератор синтетических напряжений для проверки расчетов."""

    def __init__(self, noise_rms_v: float = 0.0) -> None:
        self.noise_rms_v = noise_rms_v

    def connect(self) -> None:
        print("[dummy voltmeter] connect")

    def configure(self) -> None:
        print("[dummy voltmeter] configure")

    def measure_voltage(self, step: Optional["MeasurementStep"] = None) -> float:
        noise = random.gauss(0.0, self.noise_rms_v) if self.noise_rms_v > 0.0 else 0.0
        if step is None:
            return noise

        # Сопротивления для синтетической проверки: RA около 100 Ом, RB около 120 Ом.
        if step.kind == "vdp":
            resistance = 100.0 if step.group == "RA" else 120.0
            return step.current_a * resistance + noise

        # Hall: V(+B) = +1 мВ, V(-B) = -1 мВ для всех четырех конфигураций.
        if step.kind == "hall":
            return (1.0e-3 if step.field_polarity == +1 else -1.0e-3) + noise

        return noise

    def disconnect(self) -> None:
        print("[dummy voltmeter] disconnect")


# ---------------------------------------------------------------------------
# План измерений и результаты
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MeasurementStep:
    kind: str                     # "vdp" или "hall"
    name: str                     # например R21_34, V24_P
    force_pos: int                # контакт образца, куда подключается AMP+
    force_neg: int                # контакт образца, куда подключается AMP-
    volt_pos: int                 # контакт образца, куда подключается VOL+
    volt_neg: int                 # контакт образца, куда подключается VOL-
    current_a: float
    field_polarity: Optional[int] = None  # +1, -1 или None
    group: Optional[str] = None           # RA/RB для VdP или VC/VD/VE/VF для Hall
    switch_command: str = ""              # заполняется после подготовки плана

    @property
    def label(self) -> str:
        return self.name


@dataclass
class MeasurementRecord:
    index: int
    kind: str
    name: str
    group: Optional[str]
    field_polarity: Optional[int]
    current_a: float
    force: str
    voltage_sense: str
    switch_command: str
    voltage_v: float
    voltage_std_v: float
    readings_count: int
    t_start_ns: int
    t_end_ns: int

    @property
    def duration_ms(self) -> float:
        return (self.t_end_ns - self.t_start_ns) / 1_000_000.0


@dataclass
class MeasurementConfig:
    current_a: float
    thickness_m: float
    magnetic_field_t: float
    readings_per_point: int = 1
    measurement_settle_s: float = 0.0
    switch_relay_settle_s: float = 0.0
    repeat_count: int = 1
    run_vdp: bool = True
    run_hall: bool = True
    use_abs_resistance_for_vdp: bool = True
    switch_verify: bool = False
    open_all_on_finish: bool = True


@dataclass
class ReductionResult:
    raw_resistances_ohm: Dict[str, float] = field(default_factory=dict)
    ra_ohm: Optional[float] = None
    rb_ohm: Optional[float] = None
    sheet_resistance_ohm_per_sq: Optional[float] = None
    resistivity_ohm_m: Optional[float] = None
    resistivity_ohm_cm: Optional[float] = None
    hall_components_v: Dict[str, float] = field(default_factory=dict)
    hall_voltage_v: Optional[float] = None
    hall_coefficient_m3_per_c: Optional[float] = None
    hall_mobility_m2_per_v_s: Optional[float] = None
    hall_mobility_cm2_per_v_s: Optional[float] = None
    carrier_density_m3: Optional[float] = None
    carrier_density_cm3: Optional[float] = None
    carrier_type_hint: Optional[str] = None
    warnings: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Коммутатор
# ---------------------------------------------------------------------------


class MatrixSwitchError(RuntimeError):
    pass


class MatrixSwitch:
    """
    Быстрый контроллер 4x4 коммутатора.

    Основной метод set_route() принимает физический смысл подключения:
    force_pos/force_neg/volt_pos/volt_neg - номера контактов образца 1..4.
    """

    def __init__(
        self,
        visa_resource_name: Optional[str] = None,
        contact_to_output: Optional[Mapping[int, int]] = None,
        read_termination: str = "\r\n",
        write_termination: str = "\n",
        query_delay_s: float = 0.0,
        connect_delay_s: float = 1.0,
        relay_settle_s: float = 0.0,
        combine_open_close: bool = True,
        verify: bool = False,
        dry_run: bool = False,
        timeout_ms: int = 5000,
    ) -> None:
        self.visa_resource_name = visa_resource_name or default_visa_resource_name()
        self.contact_to_output = dict(contact_to_output or DEFAULT_CONTACT_TO_OUTPUT)
        self.read_termination = read_termination
        self.write_termination = write_termination
        self.query_delay_s = query_delay_s
        self.connect_delay_s = connect_delay_s
        self.relay_settle_s = relay_settle_s
        self.combine_open_close = combine_open_close
        self.verify = verify
        self.dry_run = dry_run
        self.timeout_ms = timeout_ms
        self.rm: Any = None
        self.inst: Any = None
        self.last_channel_list: Optional[str] = None

    def connect(self) -> None:
        if self.dry_run:
            print(f"[dry switch] connect {self.visa_resource_name}")
            return
        try:
            import pyvisa  # type: ignore
        except ImportError as exc:
            raise MatrixSwitchError("Не установлен pyvisa. Установите pyvisa или используйте --dry-run-switch") from exc

        self.rm = pyvisa.ResourceManager()
        self.inst = self.rm.open_resource(self.visa_resource_name, read_termination=self.read_termination)
        self.inst.write_termination = self.write_termination
        self.inst.query_delay = self.query_delay_s
        self.inst.timeout = self.timeout_ms
        if self.connect_delay_s > 0.0:
            time.sleep(self.connect_delay_s)

    def disconnect(self) -> None:
        if self.dry_run:
            print("[dry switch] disconnect")
            return
        if self.inst is not None:
            self.inst.close()
            self.inst = None

    def open_all(self) -> None:
        self._write("ROUTe:OPEN:ALL")
        self.last_channel_list = None
        if self.relay_settle_s > 0.0:
            time.sleep(self.relay_settle_s)

    def build_channel_list(self, force_pos: int, force_neg: int, volt_pos: int, volt_neg: int) -> str:
        contacts = [force_pos, force_neg, volt_pos, volt_neg]
        if len(set(contacts)) != 4:
            raise ValueError(f"Все 4 контакта должны быть различными, получено: {contacts}")
        for contact in contacts:
            if contact not in self.contact_to_output:
                raise ValueError(f"Контакт {contact} отсутствует в contact_to_output={self.contact_to_output}")

        vol_p_out = self.contact_to_output[volt_pos]
        vol_n_out = self.contact_to_output[volt_neg]
        amp_p_out = self.contact_to_output[force_pos]
        amp_n_out = self.contact_to_output[force_neg]

        outputs = [vol_p_out, vol_n_out, amp_p_out, amp_n_out]
        if len(set(outputs)) != 4:
            raise ValueError(f"Выходы коммутатора должны быть различными, получено: {outputs}")

        channels = [
            f"{int(SwitchInput.VOL_POSITIVE)}!{vol_p_out}",
            f"{int(SwitchInput.VOL_NEGATIVE)}!{vol_n_out}",
            f"{int(SwitchInput.AMP_POSITIVE)}!{amp_p_out}",
            f"{int(SwitchInput.AMP_NEGATIVE)}!{amp_n_out}",
        ]
        return ",".join(channels)

    def build_switch_command(self, channel_list: str) -> str:
        if self.combine_open_close:
            return f"ROUTe:OPEN:ALL;ROUTe:CLOSe (@{channel_list})"
        return f"ROUTe:CLOSe (@{channel_list})"

    def route_command_for_step(self, step: MeasurementStep) -> str:
        ch = self.build_channel_list(step.force_pos, step.force_neg, step.volt_pos, step.volt_neg)
        return self.build_switch_command(ch)

    def set_route(self, step: MeasurementStep) -> None:
        channel_list = self.build_channel_list(step.force_pos, step.force_neg, step.volt_pos, step.volt_neg)
        if self.combine_open_close:
            self._write(f"ROUTe:OPEN:ALL;ROUTe:CLOSe (@{channel_list})")
        else:
            self._write("ROUTe:OPEN:ALL")
            self._write(f"ROUTe:CLOSe (@{channel_list})")
        self.last_channel_list = channel_list

        if self.verify:
            expected = ",".join(["1"] * 4)
            got = self._query(f"ROUTe:CLOSe:STATe? (@{channel_list})").strip()
            if got != expected:
                raise MatrixSwitchError(f"Проверка коммутации не прошла: got={got!r}, expected={expected!r}")

        if self.relay_settle_s > 0.0:
            time.sleep(self.relay_settle_s)

    def benchmark_all_permutations(self, cycles: int = 5) -> Dict[str, float]:
        """
        Бенчмарк в стиле unit_test_max_performance, но без измерительных запросов.
        Это показывает верхнюю границу скорости управляющей программы.
        """
        if cycles <= 0:
            raise ValueError("cycles должен быть > 0")
        commands: List[str] = []
        for perm in itertools.permutations([0, 1, 2, 3]):
            channel_list = ",".join(f"{row}!{perm[row]}" for row in range(4))
            commands.append(self.build_switch_command(channel_list))

        self.open_all()
        total = cycles * len(commands)
        t0 = time.perf_counter_ns()
        for _ in range(cycles):
            for cmd in commands:
                self._write(cmd)
        self.open_all()
        t1 = time.perf_counter_ns()

        elapsed_s = (t1 - t0) / 1_000_000_000.0
        return {
            "cycles": float(cycles),
            "permutations_per_cycle": float(len(commands)),
            "total_configurations": float(total),
            "elapsed_s": elapsed_s,
            "avg_ms_per_configuration": (elapsed_s / total) * 1000.0,
            "configurations_per_s": total / elapsed_s if elapsed_s > 0.0 else float("inf"),
        }

    def _write(self, command: str) -> None:
        if self.dry_run:
            print(f"[dry switch] {command}")
            return
        if self.inst is None:
            raise MatrixSwitchError("Коммутатор не подключен")
        self.inst.write(command)

    def _query(self, command: str) -> str:
        if self.dry_run:
            print(f"[dry switch query] {command}")
            return "1,1,1,1"
        if self.inst is None:
            raise MatrixSwitchError("Коммутатор не подключен")
        return str(self.inst.query(command))


# ---------------------------------------------------------------------------
# Генерация плана измерений
# ---------------------------------------------------------------------------


def build_vdp_steps(current_a: float) -> List[MeasurementStep]:
    """
    8 измерений Ван-дер-Пау.

    Обозначение R_ij,kl = V_kl / I_ij, где I_ij: AMP+ на i, AMP- на j;
    V_kl: VOL+ на k, VOL- на l.
    """
    specs = [
        ("R21_34", 2, 1, 3, 4, "RA"),
        ("R12_43", 1, 2, 4, 3, "RA"),
        ("R43_12", 4, 3, 1, 2, "RA"),
        ("R34_21", 3, 4, 2, 1, "RA"),
        ("R32_41", 3, 2, 4, 1, "RB"),
        ("R23_14", 2, 3, 1, 4, "RB"),
        ("R14_23", 1, 4, 2, 3, "RB"),
        ("R41_32", 4, 1, 3, 2, "RB"),
    ]
    return [
        MeasurementStep(
            kind="vdp",
            name=name,
            force_pos=fp,
            force_neg=fn,
            volt_pos=vp,
            volt_neg=vn,
            current_a=current_a,
            field_polarity=None,
            group=group,
        )
        for name, fp, fn, vp, vn, group in specs
    ]


def build_hall_steps(current_a: float) -> List[MeasurementStep]:
    """
    8 измерений Холла: четыре конфигурации при +B и те же четыре при -B.

    V24: I13, измерение V2 - V4
    V42: I31, измерение V4 - V2
    V13: I42, измерение V1 - V3
    V31: I24, измерение V3 - V1

    После измерений рассчитывается:
        VC = V24(+B) - V24(-B)
        VD = V42(+B) - V42(-B)
        VE = V13(+B) - V13(-B)
        VF = V31(+B) - V31(-B)
        VH = (VC + VD + VE + VF) / 8
    """
    base = [
        ("V24", 1, 3, 2, 4, "VC"),
        ("V42", 3, 1, 4, 2, "VD"),
        ("V13", 4, 2, 1, 3, "VE"),
        ("V31", 2, 4, 3, 1, "VF"),
    ]
    steps: List[MeasurementStep] = []
    for polarity, suffix in [(+1, "P"), (-1, "N")]:
        for label, fp, fn, vp, vn, group in base:
            steps.append(
                MeasurementStep(
                    kind="hall",
                    name=f"{label}_{suffix}",
                    force_pos=fp,
                    force_neg=fn,
                    volt_pos=vp,
                    volt_neg=vn,
                    current_a=current_a,
                    field_polarity=polarity,
                    group=group,
                )
            )
    return steps


def with_precomputed_switch_commands(steps: Sequence[MeasurementStep], switch: MatrixSwitch) -> List[MeasurementStep]:
    prepared: List[MeasurementStep] = []
    for step in steps:
        prepared.append(
            MeasurementStep(
                kind=step.kind,
                name=step.name,
                force_pos=step.force_pos,
                force_neg=step.force_neg,
                volt_pos=step.volt_pos,
                volt_neg=step.volt_neg,
                current_a=step.current_a,
                field_polarity=step.field_polarity,
                group=step.group,
                switch_command=switch.route_command_for_step(step),
            )
        )
    return prepared


# ---------------------------------------------------------------------------
# Исполнитель измерений
# ---------------------------------------------------------------------------


class HallVdpMeasurementRunner:
    def __init__(
        self,
        switch: MatrixSwitch,
        current_source: CurrentSource,
        voltmeter: Voltmeter,
        field_controller: MagneticFieldController,
        config: MeasurementConfig,
    ) -> None:
        self.switch = switch
        self.current_source = current_source
        self.voltmeter = voltmeter
        self.field_controller = field_controller
        self.config = config
        self.records: List[MeasurementRecord] = []
        self._next_record_index = 1

    def run(self) -> Tuple[List[MeasurementRecord], ReductionResult]:
        self._connect_all()
        output_enabled = False
        try:
            self.switch.open_all()
            self.current_source.configure(self.config.current_a)
            self.current_source.set_current(self.config.current_a)
            self.current_source.output_on()
            output_enabled = True

            all_records: List[MeasurementRecord] = []
            for repeat_index in range(self.config.repeat_count):
                if self.config.repeat_count > 1:
                    print(f"\n=== Повтор {repeat_index + 1}/{self.config.repeat_count} ===")

                if self.config.run_vdp:
                    self.field_controller.set_polarity(0)
                    vdp_steps = with_precomputed_switch_commands(build_vdp_steps(self.config.current_a), self.switch)
                    all_records.extend(self._run_steps(vdp_steps))

                if self.config.run_hall:
                    hall_steps = with_precomputed_switch_commands(build_hall_steps(self.config.current_a), self.switch)
                    for polarity in (+1, -1):
                        self.field_controller.set_polarity(polarity)
                        group_steps = [step for step in hall_steps if step.field_polarity == polarity]
                        all_records.extend(self._run_steps(group_steps))

            self.records = all_records
            reduction = reduce_measurements(all_records, self.config)
            return all_records, reduction

        finally:
            if output_enabled:
                try:
                    self.current_source.output_off()
                except Exception as exc:
                    print(f"WARNING: ошибка при выключении источника тока: {exc}", file=sys.stderr)
            if self.config.open_all_on_finish:
                try:
                    self.switch.open_all()
                except Exception as exc:
                    print(f"WARNING: ошибка при ROUTe:OPEN:ALL: {exc}", file=sys.stderr)
            self._disconnect_all()

    def _connect_all(self) -> None:
        self.switch.connect()
        self.current_source.connect()
        self.voltmeter.connect()
        self.field_controller.connect()
        self.voltmeter.configure()

    def _disconnect_all(self) -> None:
        # Порядок отключения: измеритель, источник, поле, коммутатор.
        for obj, name in [
            (self.voltmeter, "voltmeter"),
            (self.current_source, "current_source"),
            (self.field_controller, "field_controller"),
            (self.switch, "switch"),
        ]:
            try:
                obj.disconnect()
            except Exception as exc:
                print(f"WARNING: ошибка при disconnect({name}): {exc}", file=sys.stderr)

    def _run_steps(self, steps: Sequence[MeasurementStep]) -> List[MeasurementRecord]:
        records: List[MeasurementRecord] = []
        for step in steps:
            record_index = self._next_record_index
            self._next_record_index += 1
            record = self._measure_one_step(record_index, step)
            records.append(record)
            print(
                f"{record.index:03d} {record.kind:4s} {record.name:8s} "
                f"I={record.current_a:g} A V={record.voltage_v:.9g} V "
                f"dt={record.duration_ms:.3f} ms"
            )
        return records

    def _measure_one_step(self, index: int, step: MeasurementStep) -> MeasurementRecord:
        t_start = time.perf_counter_ns()

        # Коммутация - главный участок, где минимизируем лишние round-trip.
        self.switch.set_route(step)

        if self.config.measurement_settle_s > 0.0:
            time.sleep(self.config.measurement_settle_s)

        readings: List[float] = []
        for _ in range(max(1, self.config.readings_per_point)):
            readings.append(float(self.voltmeter.measure_voltage(step)))

        t_end = time.perf_counter_ns()
        voltage = statistics.fmean(readings)
        std_v = statistics.pstdev(readings) if len(readings) > 1 else 0.0

        return MeasurementRecord(
            index=index,
            kind=step.kind,
            name=step.name,
            group=step.group,
            field_polarity=step.field_polarity,
            current_a=step.current_a,
            force=f"I{step.force_pos}{step.force_neg}",
            voltage_sense=f"V{step.volt_pos}{step.volt_neg}",
            switch_command=step.switch_command or self.switch.route_command_for_step(step),
            voltage_v=voltage,
            voltage_std_v=std_v,
            readings_count=len(readings),
            t_start_ns=t_start,
            t_end_ns=t_end,
        )


# ---------------------------------------------------------------------------
# Расчеты
# ---------------------------------------------------------------------------


def reduce_measurements(records: Sequence[MeasurementRecord], config: MeasurementConfig) -> ReductionResult:
    result = ReductionResult()

    # Если repeat_count > 1, усредняем одноименные точки.
    by_name: Dict[str, List[MeasurementRecord]] = {}
    for rec in records:
        by_name.setdefault(rec.name, []).append(rec)

    mean_voltage_by_name: Dict[str, float] = {
        name: statistics.fmean(rec.voltage_v for rec in recs)
        for name, recs in by_name.items()
    }

    # Ван-дер-Пау.
    vdp_names = ["R21_34", "R12_43", "R43_12", "R34_21", "R32_41", "R23_14", "R14_23", "R41_32"]
    for name in vdp_names:
        if name in mean_voltage_by_name:
            resistance = mean_voltage_by_name[name] / config.current_a
            if config.use_abs_resistance_for_vdp:
                resistance = abs(resistance)
            result.raw_resistances_ohm[name] = resistance

    ra_names = ["R21_34", "R12_43", "R43_12", "R34_21"]
    rb_names = ["R32_41", "R23_14", "R14_23", "R41_32"]
    if all(name in result.raw_resistances_ohm for name in ra_names):
        result.ra_ohm = statistics.fmean(result.raw_resistances_ohm[name] for name in ra_names)
    if all(name in result.raw_resistances_ohm for name in rb_names):
        result.rb_ohm = statistics.fmean(result.raw_resistances_ohm[name] for name in rb_names)

    if result.ra_ohm is not None and result.rb_ohm is not None:
        try:
            rs = van_der_pauw_sheet_resistance(result.ra_ohm, result.rb_ohm)
            result.sheet_resistance_ohm_per_sq = rs
            result.resistivity_ohm_m = rs * config.thickness_m
            result.resistivity_ohm_cm = result.resistivity_ohm_m * 100.0
        except ValueError as exc:
            result.warnings.append(str(exc))

    # Холл.
    required_hall = ["V24_P", "V24_N", "V42_P", "V42_N", "V13_P", "V13_N", "V31_P", "V31_N"]
    if all(name in mean_voltage_by_name for name in required_hall):
        vc = mean_voltage_by_name["V24_P"] - mean_voltage_by_name["V24_N"]
        vd = mean_voltage_by_name["V42_P"] - mean_voltage_by_name["V42_N"]
        ve = mean_voltage_by_name["V13_P"] - mean_voltage_by_name["V13_N"]
        vf = mean_voltage_by_name["V31_P"] - mean_voltage_by_name["V31_N"]
        result.hall_components_v = {"VC": vc, "VD": vd, "VE": ve, "VF": vf}
        result.hall_voltage_v = (vc + vd + ve + vf) / 8.0

        if config.magnetic_field_t == 0.0:
            result.warnings.append("magnetic_field_t=0: невозможно рассчитать коэффициент Холла и подвижность")
        else:
            result.hall_coefficient_m3_per_c = (
                result.hall_voltage_v * config.thickness_m / (config.current_a * config.magnetic_field_t)
            )
            if result.hall_coefficient_m3_per_c != 0.0:
                result.carrier_density_m3 = 1.0 / (E_CHARGE_C * abs(result.hall_coefficient_m3_per_c))
                result.carrier_density_cm3 = result.carrier_density_m3 / 1.0e6
                # Подсказка по типу носителей зависит от принятого направления B/I/нумерации контактов.
                # Поэтому это именно hint, а не абсолютное утверждение.
                result.carrier_type_hint = "p-type by this sign convention" if result.hall_coefficient_m3_per_c > 0 else "n-type by this sign convention"
            else:
                result.warnings.append("hall_coefficient=0: невозможно рассчитать концентрацию носителей")

    if result.hall_coefficient_m3_per_c is not None and result.resistivity_ohm_m is not None:
        if result.resistivity_ohm_m > 0.0:
            result.hall_mobility_m2_per_v_s = abs(result.hall_coefficient_m3_per_c / result.resistivity_ohm_m)
            result.hall_mobility_cm2_per_v_s = result.hall_mobility_m2_per_v_s * 1.0e4
        else:
            result.warnings.append("resistivity<=0: невозможно рассчитать подвижность")

    return result


def van_der_pauw_sheet_resistance(ra_ohm: float, rb_ohm: float, *, rel_tol: float = 1e-12) -> float:
    """
    Решает уравнение Ван-дер-Пау:
        exp(-pi*RA/Rs) + exp(-pi*RB/Rs) = 1
    Возвращает Rs в Ом/квадрат.
    """
    if ra_ohm <= 0.0 or rb_ohm <= 0.0:
        raise ValueError(f"RA и RB должны быть > 0, получено RA={ra_ohm}, RB={rb_ohm}")

    if math.isclose(ra_ohm, rb_ohm, rel_tol=1e-9, abs_tol=0.0):
        return math.pi * (ra_ohm + rb_ohm) / 2.0 / math.log(2.0)

    def f(rs: float) -> float:
        return math.exp(-math.pi * ra_ohm / rs) + math.exp(-math.pi * rb_ohm / rs) - 1.0

    low = max(min(ra_ohm, rb_ohm) * 1e-12, 1e-300)
    high = max(ra_ohm, rb_ohm) * math.pi / math.log(2.0)
    while f(high) <= 0.0:
        high *= 2.0
        if high > max(ra_ohm, rb_ohm) * 1e18:
            raise ValueError("Не удалось подобрать верхнюю границу для решения уравнения Ван-дер-Пау")

    # f(low) ~= -1, f(high) > 0, f монотонно возрастает по Rs.
    for _ in range(300):
        mid = (low + high) / 2.0
        val = f(mid)
        if abs(val) < rel_tol:
            return mid
        if val > 0.0:
            high = mid
        else:
            low = mid
    return (low + high) / 2.0


# ---------------------------------------------------------------------------
# Сохранение результатов
# ---------------------------------------------------------------------------


def write_records_csv(path: os.PathLike[str] | str, records: Sequence[MeasurementRecord]) -> None:
    rows: List[Dict[str, Any]] = []
    for rec in records:
        row = asdict(rec)
        row["duration_ms"] = rec.duration_ms
        rows.append(row)

    fieldnames = [
        "index",
        "kind",
        "name",
        "group",
        "field_polarity",
        "current_a",
        "force",
        "voltage_sense",
        "switch_command",
        "voltage_v",
        "voltage_std_v",
        "readings_count",
        "duration_ms",
        "t_start_ns",
        "t_end_ns",
    ]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def write_summary_json(path: os.PathLike[str] | str, config: MeasurementConfig, reduction: ReductionResult) -> None:
    payload = {
        "config": asdict(config),
        "summary": asdict(reduction),
        "created_unix_s": time.time(),
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def print_summary(reduction: ReductionResult) -> None:
    print("\n=== Итоги ===")
    if reduction.ra_ohm is not None:
        print(f"RA = {reduction.ra_ohm:.9g} Ohm")
    if reduction.rb_ohm is not None:
        print(f"RB = {reduction.rb_ohm:.9g} Ohm")
    if reduction.sheet_resistance_ohm_per_sq is not None:
        print(f"Rs = {reduction.sheet_resistance_ohm_per_sq:.9g} Ohm/sq")
    if reduction.resistivity_ohm_m is not None:
        print(f"rho = {reduction.resistivity_ohm_m:.9g} Ohm*m = {reduction.resistivity_ohm_cm:.9g} Ohm*cm")
    if reduction.hall_voltage_v is not None:
        print(f"VH = {reduction.hall_voltage_v:.9g} V")
    if reduction.hall_coefficient_m3_per_c is not None:
        print(f"RH = {reduction.hall_coefficient_m3_per_c:.9g} m^3/C")
    if reduction.hall_mobility_m2_per_v_s is not None:
        print(
            f"muH = {reduction.hall_mobility_m2_per_v_s:.9g} m^2/(V*s) "
            f"= {reduction.hall_mobility_cm2_per_v_s:.9g} cm^2/(V*s)"
        )
    if reduction.carrier_density_m3 is not None:
        print(f"n = {reduction.carrier_density_m3:.9g} 1/m^3 = {reduction.carrier_density_cm3:.9g} 1/cm^3")
    if reduction.carrier_type_hint:
        print(f"carrier type hint = {reduction.carrier_type_hint}")
    if reduction.warnings:
        print("\nWarnings:")
        for warning in reduction.warnings:
            print(f"  - {warning}")


# ---------------------------------------------------------------------------
# CLI и утилиты
# ---------------------------------------------------------------------------


def default_visa_resource_name() -> str:
    try:
        import env  # type: ignore

        return str(env.VISA_RESOURCE_NAME)
    except Exception:
        return "TCPIP::127.0.0.1::5025::SOCKET"


def parse_contact_map(text: str) -> Dict[int, int]:
    """
    Разбор строки вида "1:A,2:B,3:C,4:D" или "1:0,2:1,3:2,4:3".
    """
    output_alias = {"A": 0, "B": 1, "C": 2, "D": 3}
    result: Dict[int, int] = {}
    if not text:
        return dict(DEFAULT_CONTACT_TO_OUTPUT)
    for item in text.split(","):
        left, right = item.split(":", 1)
        contact = int(left.strip())
        r = right.strip().upper()
        output = output_alias[r] if r in output_alias else int(r)
        result[contact] = output
    if set(result.keys()) != {1, 2, 3, 4}:
        raise ValueError("contact-map должен задавать контакты 1,2,3,4")
    if set(result.values()) != {0, 1, 2, 3}:
        raise ValueError("contact-map должен задавать четыре разных выхода A-D или 0-3")
    return result


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Hall / Van der Pauw measurement runner")
    parser.add_argument("--visa-resource", default=default_visa_resource_name(), help="VISA resource коммутатора")
    parser.add_argument("--current-a", type=float, default=1e-3, help="Ток измерения, А")
    parser.add_argument("--field-t", type=float, default=0.3, help="Модуль магнитного поля, Тл")
    parser.add_argument("--thickness-m", type=float, default=5e-4, help="Толщина образца, м")
    parser.add_argument("--readings-per-point", type=int, default=1, help="Сколько отсчетов вольтметра усреднять в точке")
    parser.add_argument("--measurement-settle-s", type=float, default=0.0, help="Задержка после коммутации перед чтением V")
    parser.add_argument("--switch-relay-settle-s", type=float, default=0.0, help="Задержка после команды коммутатора")
    parser.add_argument("--repeat-count", type=int, default=1, help="Сколько раз повторить полный набор точек")
    parser.add_argument("--output-prefix", default="hall_run", help="Префикс файлов результата")
    parser.add_argument("--contact-map", default="1:A,2:B,3:C,4:D", help="Соответствие контактов образца выходам коммутатора")
    parser.add_argument("--dry-run-switch", action="store_true", help="Не подключаться к коммутатору, только печатать SCPI")
    parser.add_argument("--dummy-instruments", action="store_true", help="Использовать безопасные dummy источник/вольтметр")
    parser.add_argument("--dummy-noise-rms-v", type=float, default=0.0, help="RMS шум dummy-вольтметра")
    parser.add_argument("--no-field-prompt", action="store_true", help="Не ждать Enter при смене магнитного поля")
    parser.add_argument("--no-vdp", action="store_true", help="Не выполнять измерения Ван-дер-Пау")
    parser.add_argument("--no-hall", action="store_true", help="Не выполнять измерения Холла")
    parser.add_argument("--no-combined-switch-cmd", action="store_true", help="Делать OPEN:ALL и CLOSe двумя write()")
    parser.add_argument("--switch-verify", action="store_true", help="Проверять ROUTe:CLOSe:STATe? после каждой коммутации; медленнее")
    parser.add_argument("--signed-vdp", action="store_true", help="Не брать abs() от сопротивлений перед расчетом VdP")
    parser.add_argument("--benchmark-switch", action="store_true", help="Запустить только бенчмарк скорости коммутации")
    parser.add_argument("--benchmark-cycles", type=int, default=5, help="Количество циклов по 24 перестановки для бенчмарка")
    return parser


def create_instruments(dummy: bool, dummy_noise_rms_v: float) -> Tuple[CurrentSource, Voltmeter]:
    if dummy:
        return DummyCurrentSource(), DummyVoltmeter(noise_rms_v=dummy_noise_rms_v)
    # Пользователь дописывает классы CurrentSource и Voltmeter выше.
    return CurrentSource(), Voltmeter()


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    contact_map = parse_contact_map(args.contact_map)

    switch = MatrixSwitch(
        visa_resource_name=args.visa_resource,
        contact_to_output=contact_map,
        relay_settle_s=args.switch_relay_settle_s,
        combine_open_close=not args.no_combined_switch_cmd,
        verify=args.switch_verify,
        dry_run=args.dry_run_switch,
        query_delay_s=0.0,
        connect_delay_s=0.0 if args.dry_run_switch else 1.0,
    )

    if args.benchmark_switch:
        switch.connect()
        try:
            stats = switch.benchmark_all_permutations(cycles=args.benchmark_cycles)
        finally:
            switch.disconnect()
        print("\n=== Switch benchmark ===")
        for key, value in stats.items():
            print(f"{key}: {value:.9g}")
        return 0

    current_source, voltmeter = create_instruments(args.dummy_instruments, args.dummy_noise_rms_v)
    field_controller = ManualFieldController(prompt=not args.no_field_prompt)

    config = MeasurementConfig(
        current_a=args.current_a,
        thickness_m=args.thickness_m,
        magnetic_field_t=args.field_t,
        readings_per_point=args.readings_per_point,
        measurement_settle_s=args.measurement_settle_s,
        switch_relay_settle_s=args.switch_relay_settle_s,
        repeat_count=args.repeat_count,
        run_vdp=not args.no_vdp,
        run_hall=not args.no_hall,
        use_abs_resistance_for_vdp=not args.signed_vdp,
        switch_verify=args.switch_verify,
    )

    runner = HallVdpMeasurementRunner(
        switch=switch,
        current_source=current_source,
        voltmeter=voltmeter,
        field_controller=field_controller,
        config=config,
    )
    records, reduction = runner.run()

    prefix = Path(args.output_prefix)
    prefix.parent.mkdir(parents=True, exist_ok=True)
    raw_csv = prefix.with_name(prefix.name + "_raw.csv")
    summary_json = prefix.with_name(prefix.name + "_summary.json")
    write_records_csv(raw_csv, records)
    write_summary_json(summary_json, config, reduction)

    print_summary(reduction)
    print(f"\nRaw data: {raw_csv}")
    print(f"Summary:  {summary_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
