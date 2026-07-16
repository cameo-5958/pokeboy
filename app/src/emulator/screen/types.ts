import { Animated, PanResponder } from "react-native";

export type BattleLinkMode = "get" | "discord";
export type TelemetryEvent = { t: number; kind: string; detail: unknown };
export type BootError = { message: string; name: string; stack: string | null };
export type Unit = (value: number) => number;
export type AnimValue = Animated.Value;
export type PanHandlers = ReturnType<typeof PanResponder.create>["panHandlers"];
export type InputGroup = "buttons" | "dpad";
