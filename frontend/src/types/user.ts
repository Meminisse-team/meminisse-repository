import type { components } from "@/generated/api-types";


export type UserStage = Required<components["schemas"]["UserStage"]>;

export type UserRole = Required<components["schemas"]["UserRole"]>;

export type EducationLevel = Required<components["schemas"]["EducationLevel"]>;

export type MaritalStatus = Required<components["schemas"]["MaritalStatus"]>;

export type ConsentType = Required<components["schemas"]["ConsentType"]>;

export type ConsentGrantedBy = Required<components["schemas"]["ConsentGrantedBy"]>;

export type User = Required<components["schemas"]["UserRead"]>;

export type ConsentRecord = Required<components["schemas"]["ConsentRead"]>;
